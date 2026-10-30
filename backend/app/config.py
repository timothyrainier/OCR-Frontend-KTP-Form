"""Konfigurasi aplikasi. Semua nilai bisa diubah lewat file .env."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parents[2]


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ---- Mode OCR -------------------------------------------------------------
# "mock"       : tidak memanggil Google, hasil dibuat otomatis (untuk uji coba)
# "documentai" : memanggil Google Document AI (Form Parser)
OCR_MODE = os.getenv("OCR_MODE", "mock").strip().lower()

# ---- Google Document AI ---------------------------------------------------
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us")            # "us" atau "eu"
DOCAI_PROCESSOR_ID = os.getenv("DOCAI_PROCESSOR_ID", "")  # ID processor Form Parser
DOCAI_PROCESSOR_VERSION = os.getenv("DOCAI_PROCESSOR_VERSION", "")  # opsional
# Path ke file JSON service account (disarankan oleh Google untuk Document AI)
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
# Batas ukuran untuk 1 request online Document AI (lihat README)
DOCAI_ONLINE_MAX_MB = float(os.getenv("DOCAI_ONLINE_MAX_MB", "38"))

# ---- Zona waktu ------------------------------------------------------------
# Cloud Run berjalan di UTC; statistik "hari ini" dihitung memakai zona ini.
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Jakarta")

# ---- Akses -----------------------------------------------------------------------
# AUTH_MODE:
#   password = login nama pengguna/kata sandi milik aplikasi (AUTH_USERS)
#   iap      = Cloud Run dilindungi Identity-Aware Proxy; identitas diambil dari header IAP
AUTH_MODE = os.getenv("AUTH_MODE", "password").strip().lower()
# Batasi ke IP publik kantor (CIDR, pisahkan koma). Kosong = tidak dibatasi.
# Contoh: OFFICE_IPS=203.0.113.10/32,198.51.100.0/28
OFFICE_IPS = [x.strip() for x in os.getenv("OFFICE_IPS", "").split(",") if x.strip()]
# Jumlah proxy tepercaya di belakang IP klien pada header X-Forwarded-For (Cloud Run: 0)
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))

# ---- Login operator -----------------------------------------------------------
# Format: "user1:password1,user2:password2". Kosong = tanpa login (hanya untuk lokal).
AUTH_USERS = {
    u.split(":", 1)[0].strip(): u.split(":", 1)[1]
    for u in os.getenv("AUTH_USERS", "").split(",") if ":" in u
}
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-ganti-di-produksi")
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
ENABLE_DOCS = _bool("ENABLE_DOCS", True)

# ---- Penyimpanan file --------------------------------------------------------------
# "local" = folder data/uploads, "gcs" = Google Cloud Storage (wajib di Cloud Run)
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").strip().lower()
GCS_BUCKET = os.getenv("GCS_BUCKET", "")

# ---- Upload ----------------------------------------------------------------
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".pdf"}
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "data" / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ---- Database ----------------------------------------------------------------
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "data" / "ocr.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
# SQLite (lokal) atau PostgreSQL (produksi), contoh:
#   postgresql+psycopg2://user:pass@host:5432/ocr
#   postgresql+psycopg2://user:pass@/ocr?host=/cloudsql/PROJECT:REGION:INSTANCE   (Cloud SQL)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")
# Database tujuan untuk fitur "Kirim ke Database" (PostgreSQL/MySQL/SQLite).
# Contoh: postgresql+psycopg2://user:pass@localhost:5432/ocr
# Default: database yang sama dengan aplikasi (tabel ocr_ktp & ocr_form_fields).
EXPORT_DATABASE_URL = os.getenv("EXPORT_DATABASE_URL", "") or DATABASE_URL

# ---- Kualitas ------------------------------------------------------------------
# Di bawah ambang ini hasil ditandai "kurang akurat" dan disarankan retake.
LOW_CONFIDENCE_THRESHOLD = float(os.getenv("LOW_CONFIDENCE_THRESHOLD", "0.80"))

# ---- Khusus mode mock ------------------------------------------------------------
MOCK_FAIL_RATE = float(os.getenv("MOCK_FAIL_RATE", "0.12"))      # peluang gagal
MOCK_LOW_QUALITY_RATE = float(os.getenv("MOCK_LOW_QUALITY_RATE", "0.2"))
MOCK_DELAY_SECONDS = float(os.getenv("MOCK_DELAY_SECONDS", "1.2"))

# Kosongkan bila frontend & backend satu domain (seperti di Cloud Run)
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
