# Deploy ke Google Cloud Run

Panduan ini memakai **Cloud Shell** (terminal di browser dari Google Cloud Console), jadi tidak
perlu memasang apa pun di komputer. Seluruh proses diotomatiskan oleh `deploy-cloudrun.sh`.

## Yang akan dibuat

| Komponen | Fungsi | Lokasi | Biaya |
|---|---|---|---|
| Cloud Run `pindai-ktp` | Website + API | asia-southeast2 (Jakarta) | Kuota gratis bulanan; sangat kecil untuk demo |
| Identity-Aware Proxy (IAP) | Hanya akun Google kantor yang diizinkan | — | Gratis |
| Cloud Storage `PROJECT-pindai-uploads` | File upload (privat, maks. 30 MB per file) + replika database | Jakarta | Sangat kecil |
| Database SQLite + Litestream | Riwayat dokumen & statistik | Jakarta (di bucket yang sama) | Gratis |
| Gemini (Vertex AI), mesin default | OCR + penyusunan field langsung oleh model | endpoint `global` (bisa diubah) | Per token gambar & teks |
| Document AI Form Parser, opsional | OCR (memakai processor yang sudah ada, atau dibuat otomatis) | asia-southeast1 (Singapura) | Per halaman yang diproses |
| Service account `pindai-ktp-run` | Identitas aplikasi, **tanpa file kunci JSON** | — | Gratis |

Tidak memakai Cloud SQL, Secret Manager, maupun load balancer.

## Model akses: tidak publik

Website dilindungi **Identity-Aware Proxy (IAP)**: pengguna harus masuk dengan akun Google
terralogiq.com yang sudah didaftarkan. Orang lain ditolak oleh Google sebelum sampai ke aplikasi,
walaupun tahu alamat websitenya. Layanan tidak memakai `allUsers`.

Default-nya hanya akun yang menjalankan deploy yang boleh masuk. Tambahan anggota tim bisa diatur di
`ALLOWED_MEMBERS` (lihat “Mengelola siapa yang boleh mengakses”).

**Opsional, pembatasan IP kantor.** Secara default tidak aktif. Bila nanti ingin aplikasi hanya bisa
dibuka dari jaringan kantor, buka https://ifconfig.me dari komputer kantor untuk melihat IP publiknya,
isi `OFFICE_IPS` di `deploy-config.sh` (misalnya `203.0.113.10/32`), lalu jalankan ulang deploy.

## Izin yang dibutuhkan (pilih salah satu)

**Opsi A (disarankan):** Owner project menjalankan `admin/opsi-a-beri-peran-ke-user.sh`, yang memberi
akun pengembang peran *Editor*, *Project IAM Admin*, *Cloud Run Admin*, dan *IAP Policy Admin*.
Setelah itu pengembang bisa deploy dan update sendiri.

**Opsi B (izin minimum):** akun pengembang cukup *Editor*. Owner menjalankan dua skrip:

1. `admin/opsi-b-1-sebelum-deploy.sh`, untuk izin service account aplikasi:
   - `documentai.apiUser` (project): memanggil OCR
   - `run.builder` untuk Compute default SA (project): build saat deploy
   - `storage.objectAdmin` (hanya pada bucket aplikasi): file upload & replika database
2. `admin/opsi-b-2-setelah-deploy.sh`, untuk mengaktifkan IAP dan mendaftarkan siapa yang boleh
   mengakses (`ALLOWED_MEMBERS`).

Kedua skrip sudah berisi nilai project `demogcp-terra2021`; ubah variabel di bagian atas bila berbeda.

## Mesin OCR: Gemini dan/atau Document AI

`deploy-config.sh` mengatur mesin OCR:

```bash
OCR_MODE="gemini"                    # mesin default
OCR_ENGINES="gemini,documentai"      # yang bisa dipilih operator di halaman upload
GEMINI_MODEL="gemini-3.5-flash"
GEMINI_LOCATION="global"
```

Dengan dua mesin aktif, halaman upload dan tombol scan ulang menampilkan pilihan **Mesin OCR**,
sehingga satu KTP bisa di-scan dengan Gemini lalu dengan Document AI untuk dibandingkan saat demo.
Mesin yang dipakai tercatat di setiap hasil scan. Untuk **hanya memakai Gemini** (Document AI tidak
diaktifkan sama sekali), isi `OCR_ENGINES="gemini"`.

**Autentikasi Gemini tanpa API key (disarankan).** Skrip deploy mengaktifkan Vertex AI API dan memberi
service account `pindai-ktp-run` peran **Vertex AI User** (`roles/aiplatform.user`). Bila akun Anda belum
boleh mengatur IAM, perintahnya ikut ditulis ke `perintah-untuk-admin.sh`, sama seperti izin lain.

**Alternatif dengan API key.** Jalankan `GEMINI_API_KEY="..." bash deploy-cloudrun.sh`. Key disimpan
sebagai environment variable Cloud Run, sehingga terlihat oleh siapa pun yang bisa melihat layanan
tersebut di Console. Jangan tulis key di `deploy-config.sh` atau file lain yang ikut dibagikan, dan
regenerate key setelah demo. Dengan key, permintaan dikirim lewat Gemini API sehingga lokasi pemrosesan
tidak bisa diatur lewat `GEMINI_LOCATION`.

**Lokasi pemrosesan.** `global` paling luas ketersediaan modelnya. Untuk menjaga data di Asia Tenggara,
coba `GEMINI_LOCATION="asia-southeast1"` (atau `asia-southeast2`). Bila model tidak tersedia di lokasi
itu, aplikasi akan menampilkan pesan “Model … tidak ditemukan di lokasi …”; kembalikan ke `global`.

**Tentang angka akurasi Gemini.** Document AI memberi skor keyakinan OCR per kata. Gemini tidak punya
skor seperti itu, jadi angka akurasi untuk Gemini adalah perkiraan keyakinan dari model sendiri,
dibatasi maksimal 98%, lalu diturunkan bila nilai gagal validasi (NIK bukan 16 digit, tanggal lahir
tidak cocok dengan NIK, agama/status di luar pilihan baku, dll.). Field yang ditandai tetap perlu
dicek operator.

## Pengaturan project (`deploy-config.sh`)

Nilai untuk project demo sudah terisi, jadi biasanya tidak perlu diubah:

| Pengaturan | Nilai | Keterangan |
|---|---|---|
| `PROJECT_ID` | `demogcp-terra2021` | |
| `REGION` | `asia-southeast2` | Cloud Run, bucket, database di Jakarta |
| `DOCAI_PROCESSOR_ID` | `4211c865a97d9bbc` | Processor “KTP Form Parser” yang sudah ada |
| `DOCAI_LOCATION` | `asia-southeast1` | Lokasi processor (Singapura) |
| `ALLOWED_MEMBERS` | kosong | Kosong = hanya akun yang menjalankan deploy |
| `OFFICE_IPS` | kosong | Kosong = tidak dibatasi IP |

## Langkah deploy

1. Buka https://console.cloud.google.com dan pilih project.
2. Klik **Activate Cloud Shell** (ikon `>_` di kanan atas).
3. Di Cloud Shell, klik **⋮ → Upload**, pilih `pindai-ktp-form.zip`.
4. Jalankan:
   ```bash
   unzip -o pindai-ktp-form.zip && cd ocr-ktp-app
   gcloud config set project demogcp-terra2021
   bash deploy-cloudrun.sh
   ```
   Pada Opsi B, setelah admin menjalankan langkah 1, tambahkan `SKIP_IAM_CHECK=1` di depan perintah.
5. Tunggu sekitar 5–10 menit. Di akhir muncul alamat website.
6. Pada Opsi B, kirim `admin/opsi-b-2-setelah-deploy.sh` ke admin untuk dijalankan. Skrip deploy juga
   menuliskan perintah yang masih kurang ke `perintah-untuk-admin-setelah-deploy.sh`.
7. Buka alamat website dan masuk dengan akun Google kantor.

Email pengguna otomatis tercatat sebagai operator di setiap scan.

### Pengaturan lain

```bash
OCR_MODE=mock bash deploy-cloudrun.sh                 # uji tanpa biaya Document AI
DOCAI_PROCESSOR_ID=abc123 bash deploy-cloudrun.sh     # pilih processor tertentu (default: Form Parser yang sudah ada)
DOCAI_LOCATION=us bash deploy-cloudrun.sh             # processor di region lain
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

Cara termudah: isi `ALLOWED_MEMBERS` di `deploy-config.sh` lalu jalankan ulang deploy. Isinya bisa
orang tertentu (`user:budi@terralogiq.com`), grup (`group:tim-ce@terralogiq.com`), atau seluruh
domain (`domain:terralogiq.com`), dipisah koma. Atau tambahkan satu per satu:

```bash
# Tambah orang / grup (butuh peran IAP Policy Admin)
gcloud iap web add-iam-policy-binding --member=user:budi@terralogiq.com \
  --role=roles/iap.httpsResourceAccessor --region=asia-southeast2 \
  --resource-type=cloud-run --service=pindai-ktp

# Cabut: ganti add-iam-policy-binding menjadi remove-iam-policy-binding
# Lihat:  gcloud iap web get-iam-policy --region=asia-southeast2 --resource-type=cloud-run --service=pindai-ktp
```

Mengaktifkan atau mengubah pembatasan IP kantor: isi `OFFICE_IPS` di `deploy-config.sh`, lalu jalankan ulang deploy.

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

**“Hanya bisa diakses dari jaringan kantor” padahal sudah di kantor** (hanya bila `OFFICE_IPS` diisi). Halaman penolakan menampilkan
IP yang terdeteksi. Masukkan IP itu ke `OFFICE_IPS`, lalu jalankan ulang deploy.

**“You don't have access” dari Google.** Akun belum didaftarkan ke IAP (lihat bagian di atas).

**403 Forbidden tanpa halaman login Google.** IAP belum aktif; jalankan
`admin/opsi-b-2-setelah-deploy.sh` (atau perintah di `perintah-untuk-admin-setelah-deploy.sh`).

**IAP tidak bisa diaktifkan.** IAP langsung di Cloud Run mensyaratkan project berada di dalam
organisasi Google (terralogiq.com) dan pengguna berasal dari organisasi yang sama.

**“Ukuran file melebihi 30 MB”.** Di Cloud Run, file diunggah lewat server aplikasi yang dibatasi
32 MB per request. Ini cukup untuk foto KTP dan formulir, dan batas Document AI sendiri 20 MB.
Bila suatu saat perlu upload langsung ke bucket sampai 100 MB, minta admin memberi service account
`pindai-ktp-run` peran *Service Account Token Creator* pada dirinya sendiri, lalu deploy dengan
variabel `GCS_SIGNED_URLS=true` dan `MAX_UPLOAD_MB=100` (ubah di `deploy-cloudrun.sh`), serta pasang
aturan CORS bucket untuk alamat website.

**Melihat log:**
```bash
gcloud run services logs read pindai-ktp --region asia-southeast2 --limit 50
```

## Catatan data pribadi

Website, file, dan database berada di region Jakarta, sedangkan proses OCR berjalan di luar Jakarta:
Document AI di Singapura (asia-southeast1) dan Gemini di lokasi `GEMINI_LOCATION` (default `global`). Untuk demo gunakan data KTP contoh (dummy). Untuk pemakaian dengan data asli,
pertimbangkan ketentuan UU PDP dan kebijakan instansi terkait.
