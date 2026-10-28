"""API backend OCR KTP & Form.

Jalankan:  uvicorn app.main:app --reload --port 8000   (dari folder backend/)
Lalu buka: http://localhost:8000
"""
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db, exporters
from .ocr_engine import MIME_BY_EXT, OcrError, engine_name, process_file

app = FastAPI(title="OCR KTP & Form", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
db.init_db()

FRONTEND_DIR = config.BASE_DIR / "frontend"
DOC_TYPES = {"auto", "ktp", "form"}


# ------------------------------------------------------------------ helper
async def _save_upload(file: UploadFile) -> tuple:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(415, f"Format {ext or 'tanpa ekstensi'} tidak didukung. Gunakan JPG, PNG, TIFF, BMP, atau PDF.")
    stored = f"{uuid.uuid4().hex}{ext}"
    dest = config.UPLOAD_DIR / stored
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"Ukuran file melebihi {config.MAX_UPLOAD_MB} MB.")
            out.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "File kosong.")
    return stored, MIME_BY_EXT[ext], size


def _run_scan(doc_id: int, doc_type: str) -> dict:
    doc = db.get_document(doc_id)
    path = config.UPLOAD_DIR / doc["stored_name"]
    try:
        result = process_file(path, doc["mime_type"], doc_type, doc["filename"])
    except OcrError as e:
        result = {"status": "failed", "doc_type": doc_type if doc_type != "auto" else doc["doc_type"],
                  "error": str(e), "fields": doc["fields"] if doc["status"] == "success" else {}, "warnings": []}
    except Exception as e:  # error tak terduga tetap dicatat sebagai gagal
        result = {"status": "failed", "doc_type": doc["doc_type"], "error": f"Kesalahan sistem: {e}", "warnings": []}
    if result["doc_type"] == "auto":
        result["doc_type"] = "ktp"
    return db.save_scan_result(doc_id, result, engine_name())


def _with_flags(doc: dict) -> dict:
    if doc:
        doc["low_quality"] = doc["status"] == "success" and (doc.get("confidence") or 0) < config.LOW_CONFIDENCE_THRESHOLD
    return doc


# ------------------------------------------------------------------ API
@app.get("/api/health")
def health():
    return {"ok": True, "mode": engine_name(), "max_upload_mb": config.MAX_UPLOAD_MB,
            "allowed": sorted(config.ALLOWED_EXTENSIONS), "low_confidence_threshold": config.LOW_CONFIDENCE_THRESHOLD}


@app.post("/api/scan")
async def scan(file: UploadFile = File(...), doc_type: str = Form("auto")):
    if doc_type not in DOC_TYPES:
        raise HTTPException(400, "doc_type harus auto, ktp, atau form")
    stored, mime, size = await _save_upload(file)
    doc_id = db.create_document(file.filename, stored, mime, size, "ktp" if doc_type == "auto" else doc_type)
    from starlette.concurrency import run_in_threadpool
    return _with_flags(await run_in_threadpool(_run_scan, doc_id, doc_type))


@app.post("/api/documents/{doc_id}/retake")
async def retake(doc_id: int, file: Optional[UploadFile] = File(None), doc_type: str = Form("auto")):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Dokumen tidak ditemukan")
    if file is not None and file.filename:
        stored, mime, size = await _save_upload(file)
        (config.UPLOAD_DIR / doc["stored_name"]).unlink(missing_ok=True)
        db.update_file(doc_id, file.filename, stored, mime, size)
    from starlette.concurrency import run_in_threadpool
    return _with_flags(await run_in_threadpool(_run_scan, doc_id, doc_type if doc_type in DOC_TYPES else "auto"))


@app.get("/api/documents")
def documents(status: Optional[str] = None, doc_type: Optional[str] = None, q: Optional[str] = None,
              date_from: Optional[str] = None, date_to: Optional[str] = None,
              page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200)):
    total, items = db.list_documents(status, doc_type, q, date_from, date_to,
                                     limit=page_size, offset=(page - 1) * page_size)
    return {"total": total, "page": page, "page_size": page_size, "items": [_with_flags(d) for d in items]}


@app.get("/api/documents/{doc_id}")
def document(doc_id: int):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Dokumen tidak ditemukan")
    return _with_flags(doc)


@app.put("/api/documents/{doc_id}")
def update_document(doc_id: int, payload: dict = Body(...)):
    if not db.get_document(doc_id):
        raise HTTPException(404, "Dokumen tidak ditemukan")
    fields = payload.get("fields")
    if not isinstance(fields, dict) or not isinstance(fields.get("items"), list):
        raise HTTPException(400, "Format fields tidak valid")
    for item in fields["items"]:          # nilai yang diubah manual dianggap pasti benar
        if item.get("edited"):
            item["confidence"], item["warning"] = 1.0, None
    return _with_flags(db.update_fields(doc_id, fields, payload.get("verified")))


@app.delete("/api/documents/{doc_id}")
def remove_document(doc_id: int):
    doc = db.delete_document(doc_id)
    if not doc:
        raise HTTPException(404, "Dokumen tidak ditemukan")
    (config.UPLOAD_DIR / doc["stored_name"]).unlink(missing_ok=True)
    return {"deleted": doc_id}


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: int):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Dokumen tidak ditemukan")
    path = config.UPLOAD_DIR / doc["stored_name"]
    if not path.exists():
        raise HTTPException(404, "File asli sudah tidak tersedia")
    return FileResponse(path, media_type=doc["mime_type"], filename=doc["filename"],
                        content_disposition_type="inline")


@app.get("/api/stats")
def stats(days: int = Query(30, ge=7, le=366)):
    s = db.get_stats(days=days, low_threshold=config.LOW_CONFIDENCE_THRESHOLD)
    s["needs_attention"] = [_with_flags(d) for d in s["needs_attention"]]
    s["mode"] = engine_name()
    return s


def _export_docs(ids: Optional[str], status, doc_type, q, date_from, date_to):
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()] if ids else None
    _, docs = db.list_documents(status, doc_type, q, date_from, date_to, ids=id_list, limit=100000)
    return docs


@app.get("/api/export")
def export(format: str = Query("xlsx", pattern="^(csv|xlsx|json|sql)$"), ids: Optional[str] = None,
           status: Optional[str] = None, doc_type: Optional[str] = None, q: Optional[str] = None,
           date_from: Optional[str] = None, date_to: Optional[str] = None):
    docs = _export_docs(ids, status, doc_type, q, date_from, date_to)
    if not docs:
        raise HTTPException(404, "Tidak ada dokumen untuk diekspor")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    data, mime = {
        "csv": (exporters.to_csv, "text/csv; charset=utf-8"),
        "xlsx": (exporters.to_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "json": (exporters.to_json, "application/json"),
        "sql": (exporters.to_sql, "application/sql"),
    }[format]
    return Response(data(docs), media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="hasil-ocr-{stamp}.{format}"'})


@app.post("/api/export/database")
def export_database(payload: dict = Body(default={})):
    ids = payload.get("ids")
    docs = _export_docs(",".join(map(str, ids)) if ids else None, payload.get("status"), payload.get("doc_type"),
                        None, None, None)
    if not docs:
        raise HTTPException(404, "Tidak ada dokumen untuk dikirim")
    try:
        return exporters.push_to_database(docs, config.EXPORT_DATABASE_URL)
    except Exception as e:
        raise HTTPException(500, f"Gagal menyimpan ke database: {e}")


# ------------------------------------------------------------------ frontend
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
