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

# ---- Upload ----------------------------------------------------------------
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".pdf"}
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "data" / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ---- Database ----------------------------------------------------------------
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "data" / "ocr.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
# Database tujuan untuk fitur "Kirim ke Database" (PostgreSQL/MySQL/SQLite).
# Contoh: postgresql+psycopg2://user:pass@localhost:5432/ocr
EXPORT_DATABASE_URL = os.getenv("EXPORT_DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'export.db'}")

# ---- Kualitas ------------------------------------------------------------------
# Di bawah ambang ini hasil ditandai "kurang akurat" dan disarankan retake.
LOW_CONFIDENCE_THRESHOLD = float(os.getenv("LOW_CONFIDENCE_THRESHOLD", "0.80"))

# ---- Khusus mode mock ------------------------------------------------------------
MOCK_FAIL_RATE = float(os.getenv("MOCK_FAIL_RATE", "0.12"))      # peluang gagal
MOCK_LOW_QUALITY_RATE = float(os.getenv("MOCK_LOW_QUALITY_RATE", "0.2"))
MOCK_DELAY_SECONDS = float(os.getenv("MOCK_DELAY_SECONDS", "1.2"))

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
