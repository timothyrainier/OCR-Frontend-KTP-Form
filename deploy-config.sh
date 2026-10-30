# =============================================================================
#  Pengaturan deploy untuk project demo DJKN. Dibaca otomatis oleh deploy-cloudrun.sh.
#  Nilai yang diberikan langsung di depan perintah tetap menang, misalnya:
#     ALLOWED_MEMBERS="domain:terralogiq.com" bash deploy-cloudrun.sh
# =============================================================================
PROJECT_ID="${PROJECT_ID:-demogcp-terra2021}"
REGION="${REGION:-asia-southeast2}"                    # Cloud Run, bucket, database: Jakarta

# Mesin OCR. Default Gemini; Document AI tetap bisa dipilih di halaman upload untuk perbandingan.
# Hanya Gemini (tanpa Document AI sama sekali):  OCR_ENGINES="gemini"
OCR_MODE="${OCR_MODE:-gemini}"
OCR_ENGINES="${OCR_ENGINES:-gemini,documentai}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"
GEMINI_LOCATION="${GEMINI_LOCATION:-global}"
# JANGAN tulis API key di file ini. Tanpa key, Gemini dipanggil dengan service account aplikasi.

# Processor "KTP Form Parser" yang sudah ada (Document AI > My processors)
DOCAI_PROCESSOR_ID="${DOCAI_PROCESSOR_ID:-4211c865a97d9bbc}"
DOCAI_LOCATION="${DOCAI_LOCATION:-asia-southeast1}"     # Singapura

# Siapa yang boleh membuka aplikasi. Kosong = hanya akun yang menjalankan deploy.
# Contoh: "user:a@terralogiq.com,user:b@terralogiq.com" atau "domain:terralogiq.com"
ALLOWED_MEMBERS="${ALLOWED_MEMBERS:-}"

# IP publik kantor (opsional). Kosong = tidak dibatasi IP; akses tetap hanya lewat akun Google (IAP).
# Isi nanti bila sudah tahu, contoh: "203.0.113.10/32"
OFFICE_IPS="${OFFICE_IPS:-}"
