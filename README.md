# Pindai KTP & Form — OCR dengan Gemini dan Google Document AI

Website untuk memindai (OCR) KTP dan formulir: pilih file, scan, lihat hasil yang tersusun per field,
koreksi manual, scan ulang (retake) bila gagal atau kurang akurat, lalu ekspor ke CSV, Excel, JSON,
SQL, atau langsung ke database. Ada halaman Monitoring untuk rekap harian dan bulanan.

Saat ini berjalan dalam **mode mock** (tanpa Google). Begitu kredensial Document AI siap,
cukup ubah isi `.env`, tidak perlu mengubah kode.

**Deploy ke Google Cloud Run:** lihat [DEPLOY-CLOUDRUN.md](DEPLOY-CLOUDRUN.md). Satu perintah
(`bash deploy-cloudrun.sh` di Cloud Shell) menyiapkan semuanya.

## Menjalankan

Butuh Python 3.10 atau lebih baru.

```bash
# Linux / macOS
./run.sh
# Windows
run.bat
```

Lalu buka **http://localhost:8000**. Skrip otomatis membuat `.env` dari `.env.example`,
membuat virtualenv, dan memasang dependensi.

Ingin dashboard langsung berisi data contoh (75 hari terakhir)?

```bash
cd backend
python seed_demo.py --reset
```

Cara manual:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cd backend && uvicorn app.main:app --reload --port 8000
```

## Struktur

```
backend/
  app/main.py          API FastAPI + melayani frontend
  app/ocr_engine.py    Mode mock & pemanggilan Document AI (Form Parser)
  app/ktp_parser.py    Menyusun teks KTP menjadi field + validasi (NIK, tanggal, jenis kelamin, provinsi)
  app/db.py            Tabel documents & scan_logs (SQLite lokal / PostgreSQL produksi), statistik
  app/storage.py       Penyimpanan file: folder lokal atau Google Cloud Storage
  app/auth.py          Login operator (cookie bertanda tangan)
  app/exporters.py     CSV, Excel, JSON, SQL, dan kirim ke database
  app/config.py        Semua pengaturan dari .env
  seed_demo.py         Pengisi data contoh
backend/app/gemini_engine.py  Mesin OCR Gemini (prompt + skema JSON)
frontend/index.html    Seluruh tampilan (tanpa build step)
Dockerfile, start.sh   Image Cloud Run (termasuk Litestream untuk replikasi SQLite)
litestream.yml         Konfigurasi replikasi database ke Cloud Storage
deploy-cloudrun.sh     Skrip deploy otomatis
admin/                 Skrip izin yang dijalankan admin/Owner project
data/                  Dibuat otomatis: ocr.db, export.db, uploads/
```

## Fitur

**Scan.** Tarik-lepas atau pilih banyak file sekaligus (JPG, PNG, TIFF, BMP, PDF, maksimal 100 MB
per file). Jenis dokumen bisa dideteksi otomatis atau dipilih KTP/Form.

**Hasil KTP** ditampilkan dalam tata letak kartu KTP: Provinsi, Kabupaten/Kota, NIK, Nama, Tempat
dan Tanggal Lahir, Jenis Kelamin, Gol. Darah, Alamat, RT/RW, Kel/Desa, Kecamatan, Agama, Status
Perkawinan, Pekerjaan, Kewarganegaraan, Berlaku Hingga, serta kota dan tanggal terbit. Setiap field
punya titik warna akurasi dan pesan peringatan. Parser backend juga memeriksa silang:

- NIK harus 16 digit; huruf yang salah baca (O→0, I→1, B→8, S→5) dikoreksi otomatis
- 2 digit awal NIK dicocokkan dengan nama provinsi
- Digit tanggal di NIK dicocokkan dengan tanggal lahir dan jenis kelamin (perempuan +40)
- Agama, status perkawinan, jenis kelamin dicocokkan ke pilihan baku (fuzzy match)

**Hasil Form** ditampilkan sebagai pasangan label–nilai per halaman, plus tabel yang ditemukan Form Parser.

**Retake.** Jika scan gagal, muncul peringatan merah dengan penyebabnya. Jika akurasi di bawah
`LOW_CONFIDENCE_THRESHOLD` (default 80%), muncul peringatan kuning. Keduanya punya tombol
*Scan ulang* (file sama, bisa ganti jenis dokumen) dan *Ganti foto & scan*. Setiap percobaan dicatat.

**Koreksi manual.** Semua field bisa diedit, lalu *Simpan perubahan*. Field yang diedit dianggap
akurasi 100%. Ada tombol *Tandai terverifikasi*.

**Riwayat.** Cari berdasarkan nama file, nama, atau NIK; filter status (berhasil, akurasi rendah,
gagal), jenis, dan rentang tanggal; pilih beberapa baris untuk diekspor atau dihapus.

**Monitoring.** Dokumen dan scan hari ini dan bulan ini (berhasil vs gagal), total dokumen, tingkat
keberhasilan scan, jumlah gagal, akurasi rendah, akurasi rata-rata, jumlah retake, grafik per hari
(14/30/90 hari) dan per bulan (12 bulan), komposisi KTP vs Form, penyebab gagal terbanyak, dan
daftar dokumen yang perlu ditindaklanjuti.

> Catatan definisi: **dokumen** = satu file yang diunggah. **Scan** = satu percobaan OCR.
> Satu dokumen yang di-retake 2 kali menghasilkan 3 scan.

**Ekspor.** Excel (sheet KTP, Form, Tabel Form, Ringkasan), CSV (UTF-8 dengan BOM agar rapi di
Excel), JSON (lengkap dengan akurasi per field), SQL (`CREATE TABLE` + `INSERT`), dan
*Kirim ke database* yang melakukan upsert ke `EXPORT_DATABASE_URL` (tabel `ocr_ktp` dan
`ocr_form_fields`). Default-nya SQLite di `data/export.db`; untuk PostgreSQL/MySQL isi URL-nya dan
pasang driver yang sesuai di `requirements.txt`.

## Memakai Gemini

Isi `.env`:

```
OCR_MODE=gemini
GCP_PROJECT_ID=project-anda
GEMINI_MODEL=gemini-3.5-flash
GEMINI_LOCATION=global
# Opsional, agar operator bisa memilih mesin: OCR_ENGINES=gemini,documentai
```

Di komputer sendiri, login sekali dengan `gcloud auth application-default login` (akun Anda perlu
peran Vertex AI User). Atau isi `GEMINI_API_KEY` di `.env` (file ini tidak ikut Git maupun image Docker).

Gemini menerima gambar KTP/formulir langsung dan mengembalikan JSON per field. Hasilnya tetap
dirapikan dan divalidasi dengan aturan yang sama seperti Document AI (`ktp_parser.build_ktp`). TIFF
dan BMP dikonversi otomatis, TIFF multi-halaman dijadikan PDF, dan gambar besar diperkecil ke
`GEMINI_MAX_IMAGE_PX` (default 2400 px) supaya cepat.

## Memasang Google Document AI

Penting: Document AI **tidak memakai API key biasa**. Autentikasinya memakai
*Application Default Credentials*, biasanya berupa file JSON service account.

1. Di Google Cloud Console, buat atau pilih project dan aktifkan billing.
2. Aktifkan **Document AI API**.
3. Buka Document AI → *Create processor* → pilih **Form Parser**. Pilih region terdekat, misalnya `asia-southeast1` (Singapura).
   Catat **Processor ID** dan region.
4. IAM → *Service Accounts* → buat service account, beri peran **Document AI API User**.
   Buat key bertipe JSON, simpan sebagai `credentials/service-account.json`.
5. Isi `.env`:
   ```
   OCR_MODE=documentai
   GCP_PROJECT_ID=project-anda
   GCP_LOCATION=asia-southeast1
   DOCAI_PROCESSOR_ID=abc123def456
   GOOGLE_APPLICATION_CREDENTIALS=./credentials/service-account.json
   ```
6. Restart server. Label di pojok kanan atas berubah menjadi “Google Document AI aktif”.

**Batas Document AI untuk proses langsung (online):** 20 MB dan 15 halaman per request untuk Form
Parser. Aplikasi ini menerima upload sampai 100 MB saat dijalankan lokal (30 MB di Cloud Run); gambar yang melebihi batas Google dikompres
otomatis sebelum dikirim. PDF yang melebihi batas akan gagal dengan pesan yang jelas. Untuk PDF
besar atau banyak halaman, langkah berikutnya adalah menambahkan *batch processing* (upload ke
Cloud Storage), yang batasnya jauh lebih besar.

## API

| Metode | Endpoint | Fungsi |
|---|---|---|
| GET | `/api/health` | Mode aktif & batas upload |
| POST | `/api/auth/login`, `/api/auth/logout`, GET `/api/auth/me` | Login operator |
| POST | `/api/uploads/init` | Minta signed URL untuk upload langsung ke Cloud Storage (bila `GCS_SIGNED_URLS=true`) |
| POST | `/api/scan` | Upload multipart + scan (`file`, `doc_type` = auto/ktp/form) |
| POST | `/api/scan/object` | Scan file yang sudah diunggah ke Cloud Storage |
| POST | `/api/documents/{id}/retake`, `/retake/object` | Scan ulang; opsional file baru dan `doc_type` |
| GET | `/api/documents` | Daftar + filter (`status`, `doc_type`, `q`, `date_from`, `date_to`, `page`) |
| GET/PUT/DELETE | `/api/documents/{id}` | Lihat, simpan koreksi (`fields`, `verified`), hapus |
| GET | `/api/documents/{id}/file` | File asli |
| GET | `/api/stats?days=30` | Data monitoring |
| GET | `/api/export?format=csv\|xlsx\|json\|sql` | Ekspor (pakai `ids=1,2,3` atau filter yang sama dengan daftar) |
| POST | `/api/export/database` | Upsert ke `EXPORT_DATABASE_URL` |

Dokumentasi interaktif tersedia di http://localhost:8000/docs (dimatikan di Cloud Run).

## Mode demo di browser

`frontend/index.html` juga bisa dibuka tanpa backend. Jika `/api/health` tidak bisa diakses,
halaman otomatis masuk **mode demo**: OCR disimulasikan di browser, data contoh dibuat, dan semua
tersimpan di localStorage. Berguna untuk melihat alur dan tampilan.

## Login

Isi `AUTH_USERS=user:sandi,user2:sandi2` dan `SECRET_KEY` untuk mengaktifkan halaman login.
Jika `AUTH_USERS` kosong, aplikasi berjalan tanpa login (hanya untuk dicoba di komputer sendiri).
Di Cloud Run, defaultnya website **tidak publik**: akses lewat Identity-Aware Proxy (akun Google
kantor) dan bisa dibatasi ke IP kantor dengan `OFFICE_IPS`. Lihat DEPLOY-CLOUDRUN.md.
