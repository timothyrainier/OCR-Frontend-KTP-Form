# Deploy ke Google Cloud Run

Panduan ini memakai **Cloud Shell** (terminal di browser dari Google Cloud Console), jadi tidak
perlu memasang apa pun di komputer. Seluruh proses diotomatiskan oleh `deploy-cloudrun.sh`.

## Yang akan dibuat

| Komponen | Fungsi | Lokasi | Biaya |
|---|---|---|---|
| Cloud Run `pindai-ktp` | Website + API | asia-southeast2 (Jakarta) | Kuota gratis bulanan; sangat kecil untuk demo |
| Identity-Aware Proxy (IAP) | Hanya akun Google kantor yang diizinkan | — | Gratis |
| Cloud Storage `PROJECT-pindai-uploads` | File upload (privat) + replika database | Jakarta | Sangat kecil |
| Database SQLite + Litestream | Riwayat dokumen & statistik | Jakarta (di bucket yang sama) | Gratis |
| Document AI Form Parser `pindai-form-parser` | OCR | `us` (hanya tersedia di us/eu) | Per halaman yang diproses |
| Service account `pindai-ktp-run` | Identitas aplikasi, **tanpa file kunci JSON** | — | Gratis |

Tidak memakai Cloud SQL, Secret Manager, maupun load balancer.

## Model akses: hanya kantor, tidak publik

1. **Identity-Aware Proxy (IAP)**: pengguna harus masuk dengan akun Google terralogiq.com yang
   diizinkan. Orang lain ditolak oleh Google sebelum sampai ke aplikasi. Layanan tidak memakai
   `allUsers`.
2. **Pembatasan IP kantor** (`OFFICE_IPS`): akses dari luar jaringan kantor ditolak dengan pesan
   “Hanya bisa diakses dari jaringan kantor”, walaupun akunnya diizinkan.

Cari IP publik kantor dengan membuka https://ifconfig.me dari komputer kantor, lalu pastikan ke tim
IT bahwa IP tersebut statis dan apakah ada lebih dari satu.

## Izin yang dibutuhkan (pilih salah satu)

**Opsi A (disarankan):** Owner project menjalankan `admin/opsi-a-beri-peran-ke-user.sh`, yang memberi
akun pengembang peran *Editor*, *Project IAM Admin*, *Cloud Run Admin*, dan *IAP Policy Admin*.
Setelah itu pengembang bisa deploy dan update sendiri.

**Opsi B (izin minimum):** akun pengembang cukup *Editor*. Owner menjalankan dua skrip:

1. `admin/opsi-b-1-sebelum-deploy.sh`, untuk izin service account aplikasi:
   - `documentai.apiUser` (project): memanggil OCR
   - `iam.serviceAccountTokenCreator` (pada service account itu sendiri): link upload sementara
   - `run.builder` untuk Compute default SA (project): build saat deploy
   - `storage.objectAdmin` (hanya pada bucket aplikasi): file upload & replika database
2. `admin/opsi-b-2-setelah-deploy.sh`, untuk mengaktifkan IAP dan mendaftarkan siapa yang boleh
   mengakses (`ALLOWED_MEMBERS`).

Kedua skrip sudah berisi nilai project `demogcp-terra2021`; ubah variabel di bagian atas bila berbeda.

## Langkah deploy

1. Buka https://console.cloud.google.com dan pilih project.
2. Klik **Activate Cloud Shell** (ikon `>_` di kanan atas).
3. Di Cloud Shell, klik **⋮ → Upload**, pilih `pindai-ktp-form.zip`.
4. Jalankan:
   ```bash
   unzip -o pindai-ktp-form.zip && cd ocr-ktp-app
   gcloud config set project demogcp-terra2021
   OFFICE_IPS="IP.KANTOR/32" ALLOWED_MEMBERS="domain:terralogiq.com" bash deploy-cloudrun.sh
   ```
   Pada Opsi B, setelah admin menjalankan langkah 1, tambahkan `SKIP_IAM_CHECK=1` di depan perintah.
5. Tunggu sekitar 5–10 menit. Di akhir muncul alamat website.
6. Pada Opsi B, kirim `admin/opsi-b-2-setelah-deploy.sh` ke admin untuk dijalankan. Skrip deploy juga
   menuliskan perintah yang masih kurang ke `perintah-untuk-admin-setelah-deploy.sh`.
7. Buka alamat website dari jaringan kantor dan masuk dengan akun Google kantor.

`ALLOWED_MEMBERS` bisa berisi seluruh domain (`domain:terralogiq.com`), grup
(`group:tim-ce@terralogiq.com`, paling mudah dikelola), atau orang tertentu (`user:budi@terralogiq.com`),
dipisah koma. Email pengguna otomatis tercatat sebagai operator di setiap scan.

### Pengaturan lain

```bash
OCR_MODE=mock bash deploy-cloudrun.sh                 # uji tanpa biaya Document AI
DOCAI_PROCESSOR_ID=abc123 bash deploy-cloudrun.sh     # pakai processor yang sudah ada
DB_MODE=cloudsql bash deploy-cloudrun.sh              # PostgreSQL Cloud SQL (berbayar, untuk produksi)
DB_MODE=external EXTERNAL_DATABASE_URL='postgresql://...' bash deploy-cloudrun.sh   # Neon, dll.
```

## Tentang database (SQLite + Litestream)

Database SQLite berada di dalam container Cloud Run, dan setiap perubahan direplikasi setiap 1 detik
ke `gs://PROJECT-pindai-uploads/db/` oleh Litestream. Saat container baru menyala, database dipulihkan
otomatis dari sana. Karena itu Cloud Run dibatasi **1 instance** (cukup untuk puluhan pengguna
bersamaan) dan CPU selalu aktif selama instance hidup. Instance tetap mati sendiri bila tidak ada
pengunjung, sehingga biayanya sangat kecil.

Untuk produksi dengan banyak pengguna, pindah ke `DB_MODE=cloudsql`. Data lama tidak ikut pindah
otomatis; ekspor dulu bila diperlukan.

## Update aplikasi

Jalankan lagi perintah deploy yang sama. Resource yang sudah ada tidak dibuat ulang dan data tetap aman.

## Mengelola siapa yang boleh mengakses

```bash
# Tambah orang / grup (butuh peran IAP Policy Admin)
gcloud iap web add-iam-policy-binding --member=user:budi@terralogiq.com \
  --role=roles/iap.httpsResourceAccessor --region=asia-southeast2 \
  --resource-type=cloud-run --service=pindai-ktp

# Cabut: ganti add-iam-policy-binding menjadi remove-iam-policy-binding
# Lihat:  gcloud iap web get-iam-policy --region=asia-southeast2 --resource-type=cloud-run --service=pindai-ktp
```

Mengubah IP kantor cukup dengan menjalankan ulang deploy dengan `OFFICE_IPS` yang baru.

## Membersihkan setelah demo

```bash
gcloud run services delete pindai-ktp --region=asia-southeast2
gcloud storage rm -r gs://demogcp-terra2021-pindai-uploads
gcloud iam service-accounts delete pindai-ktp-run@demogcp-terra2021.iam.gserviceaccount.com
```
Processor Document AI tidak dikenai biaya bila tidak dipakai; hapus lewat Console bila tidak diperlukan.

## Pemecahan masalah

**`setIamPolicy` / “does not have permission”.** Akun Anda belum boleh memberi izin IAM. Skrip
berhenti dan menulis `perintah-untuk-admin.sh`. Pakai Opsi A atau Opsi B di atas.

**“Hanya bisa diakses dari jaringan kantor” padahal sudah di kantor.** Halaman penolakan menampilkan
IP yang terdeteksi. Masukkan IP itu ke `OFFICE_IPS`, lalu jalankan ulang deploy.

**“You don't have access” dari Google.** Akun belum didaftarkan ke IAP (lihat bagian di atas).

**403 Forbidden tanpa halaman login Google.** IAP belum aktif; jalankan
`admin/opsi-b-2-setelah-deploy.sh` (atau perintah di `perintah-untuk-admin-setelah-deploy.sh`).

**IAP tidak bisa diaktifkan.** IAP langsung di Cloud Run mensyaratkan project berada di dalam
organisasi Google (terralogiq.com) dan pengguna berasal dari organisasi yang sama.

**Upload gagal (403 / CORS).** Jalankan ulang deploy; langkah 8 memasang aturan CORS bucket.

**Melihat log:**
```bash
gcloud run services logs read pindai-ktp --region asia-southeast2 --limit 50
```

## Catatan data pribadi

Website, file, dan database berada di region Jakarta, tetapi proses OCR Document AI berjalan di
region `us`. Untuk demo gunakan data KTP contoh (dummy). Untuk pemakaian dengan data asli,
pertimbangkan ketentuan UU PDP dan kebijakan instansi terkait.
