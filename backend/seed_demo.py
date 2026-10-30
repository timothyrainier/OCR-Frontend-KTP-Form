"""Isi database dengan riwayat scan contoh (75 hari terakhir) agar dashboard bisa langsung dicoba.

    python seed_demo.py            # tambah data contoh
    python seed_demo.py --reset    # hapus SEMUA dokumen & log lalu isi ulang

Bekerja untuk SQLite maupun PostgreSQL (DATABASE_URL) dan penyimpanan lokal maupun GCS.
"""

import random
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageDraw

from app import config
config.MOCK_DELAY_SECONDS = 0
config.OCR_MODE = "mock"

from app import db  # noqa: E402
from app.ocr_engine import OcrError, process_file  # noqa: E402
from app.storage import get_storage, new_object_name  # noqa: E402

db.init_db()
if "--reset" in sys.argv:
    with db.engine.begin() as c:
        c.execute(db.scan_logs.delete())
        c.execute(db.documents.delete())


def placeholder(name) -> str:
    img = Image.new("RGB", (860, 540), (214, 230, 245))
    d = ImageDraw.Draw(img)
    d.rectangle((20, 20, 840, 520), outline=(31, 95, 173), width=4)
    d.text((40, 40), f"CONTOH DOKUMEN\n{name}", fill=(27, 36, 51))
    tmp = Path(tempfile.mkstemp(suffix=".png")[1])
    img.save(tmp)
    obj = new_object_name(".png")
    get_storage().put_file(tmp, obj, "image/png")
    return obj


now = db.now_local()
count = 0
for day in range(75, -1, -1):
    base = now - timedelta(days=day)
    for _ in range(random.randint(2, 14) if base.weekday() < 5 else random.randint(0, 4)):
        ts = min(base.replace(hour=random.randint(8, 17), minute=random.randint(0, 59), second=0), now)
        kind = random.choice(["ktp", "ktp", "form"])
        name = f"{kind}_{ts:%Y%m%d}_{random.randint(100, 999)}.png"
        obj = placeholder(name)
        doc_id = db.create_document(name, obj, "image/png", 120_000, kind, ts.isoformat(timespec="seconds"), "contoh")
        for attempt in range(random.choice([1, 1, 1, 2, 3])):
            t = (ts + timedelta(minutes=attempt * 3)).isoformat(timespec="seconds")
            try:
                r = process_file(b"", "image/png", kind, name)
            except OcrError as e:
                r = {"status": "failed", "doc_type": kind, "error": str(e), "warnings": []}
            r["duration_ms"] = random.randint(900, 2600)
            db.save_scan_result(doc_id, r, "mock", t, "contoh")
            if r["status"] == "success" and r["confidence"] >= 0.8:
                break
        count += 1
print(f"Selesai: {count} dokumen contoh ditambahkan.")
