# Deploy ke Google Cloud Run

Panduan ini memakai **Cloud Shell** (terminal di browser dari Google Cloud Console), jadi Anda tidak
perlu memasang apa pun di komputer. Seluruh proses diotomatiskan oleh `deploy-cloudrun.sh`.

## Yang akan dibuat

| Komponen | Fungsi | Lokasi |
|---|---|---|
| Cloud Run `pindai-ktp` | Menjalankan website + API | asia-southeast2 (Jakarta) |
| Cloud Storage `PROJECT-pindai-uploads` | Menyimpan file KTP/form yang diunggah (privat) | Jakarta |
| Cloud SQL PostgreSQL `pindai-db` | Riwayat dokumen & statistik | Jakarta |
| Document AI Form Parser `pindai-form-parser` | OCR | `us` (Document AI hanya tersedia di us/eu) |
| Secret Manager | Kata sandi database, kunci sesi, akun login | global |
| Service account `pindai-ktp-run` | Identitas aplikasi; **tidak perlu file kunci JSON** | — |

## Langkah deploy

1. Buka https://console.cloud.google.com, pilih project yang memiliki credits/billing aktif.
2. Klik ikon **Activate Cloud Shell** (kanan atas, ikon `>_`).
3. Di Cloud Shell, klik menu **⋮ → Upload**, pilih `pindai-ktp-form.zip`.
4. Jalankan:
   ```bash
   unzip -o pindai-ktp-form.zip && cd ocr-ktp-app
   gcloud config set project ID_PROJECT_ANDA
   bash deploy-cloudrun.sh
   ```
5. Tunggu sekitar 10–15 menit (pembuatan Cloud SQL paling lama). Di akhir akan muncul:
   ```
   SELESAI!
   Alamat aplikasi : https://pindai-ktp-xxxxx.asia-southeast2.run.app
   Login           : admin / Xy7...
   ```
   **Simpan kata sandi admin** — hanya ditampilkan sekali.

Buka alamat tersebut, masuk dengan akun admin, lalu coba scan sebuah KTP.

### Pilihan saat menjalankan skrip

Semua pengaturan bisa diberikan di depan perintah, misalnya:

```bash
# Uji dulu tanpa biaya Document AI (OCR simulasi)
OCR_MODE=mock bash deploy-cloudrun.sh

# Pakai processor Form Parser yang sudah Anda buat sendiri
DOCAI_PROCESSOR_ID=abc123def456 bash deploy-cloudrun.sh

# Pakai PostgreSQL gratis dari Neon/Supabase alih-alih Cloud SQL
DB_MODE=external EXTERNAL_DATABASE_URL='postgresql://user:pass@host/db?sslmode=require' bash deploy-cloudrun.sh

# Nama admin lain
ADMIN_USER=supervisor bash deploy-cloudrun.sh
```

## Update aplikasi

Setelah mengubah kode, jalankan lagi `bash deploy-cloudrun.sh` dari folder proyek. Resource yang sudah
ada tidak dibuat ulang, data tetap aman, dan kata sandi tidak berubah.

## Mengelola akun login

Akun disimpan di secret `pindai-ktp-auth-users` dengan format `user:sandi,user2:sandi2`.

```bash
# Lihat akun saat ini
gcloud secrets versions access latest --secret=pindai-ktp-auth-users

# Ganti / tambah akun (tulis daftar lengkap)
printf 'admin:SandiBaru123,operator1:SandiOperator1' | \
  gcloud secrets versions add pindai-ktp-auth-users --data-file=-

# Terapkan (membuat revisi baru agar secret terbaru terbaca)
gcloud run services update pindai-ktp --region asia-southeast2 \
  --update-env-vars RELOAD_AT=$(date +%s)
```

Gunakan kata sandi minimal 12 karakter. Setelah 8 kali salah masuk dari IP yang sama, login dikunci 15 menit.

## Perkiraan biaya (dipotong dari credits)

Harga bisa berubah, cek halaman harga resmi Google Cloud untuk angka terbaru.

- **Cloud Run**: ada kuota gratis bulanan; untuk pemakaian kantor biasanya Rp0 atau sangat kecil.
  `min-instances 0` berarti tidak ada biaya saat tidak dipakai, tetapi akses pertama setelah lama
  diam butuh beberapa detik untuk “bangun”.
- **Cloud SQL db-f1-micro**: biaya tetap bulanan (perkiraan sekitar US$10/bulan termasuk penyimpanan),
  karena database menyala terus. Ini komponen termahal saat pemakaian rendah. Alternatif gratis: `DB_MODE=external` dengan Neon.
- **Document AI Form Parser**: dihitung per halaman yang diproses. Retake juga dihitung sebagai halaman baru.
- **Cloud Storage**: sangat kecil untuk ukuran file KTP.

Disarankan memasang **Budget alert** di Billing → Budgets & alerts.

## Pemecahan masalah

**`--allow-unauthenticated` ditolak / “organization policy”.** Organisasi Google Workspace Anda
membatasi akses publik. Minta admin mengizinkan `allUsers` untuk Cloud Run, atau pakai project di luar
organisasi. (Aplikasi tetap terlindungi oleh login sendiri.)

**Build gagal karena izin.** Beri peran build ke service account Compute default:
```bash
PN=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')
gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
  --member="serviceAccount:${PN}-compute@developer.gserviceaccount.com" --role=roles/run.builder
```

**Upload gagal (403 / CORS) di browser.** Jalankan ulang skrip; langkah 8 memasang aturan CORS bucket
sesuai alamat Cloud Run.

**“Kesalahan sistem: … signBlob …”.** Service account belum punya peran *Service Account Token Creator*
pada dirinya sendiri. Jalankan ulang skrip.

**Melihat log.** Console → Cloud Run → `pindai-ktp` → tab *Logs*, atau:
```bash
gcloud run services logs read pindai-ktp --region asia-southeast2 --limit 50
```

## Catatan data pribadi

KTP adalah data pribadi (UU PDP). Website, file, dan database disimpan di region Jakarta, tetapi
proses OCR Document AI berjalan di region `us` atau `eu`. Pertimbangkan hal ini sesuai kebijakan
perusahaan Anda. File upload tidak terhapus otomatis; bila perlu, pasang *lifecycle rule* di bucket
untuk menghapus file setelah sekian hari.
