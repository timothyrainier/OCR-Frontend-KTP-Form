"""API backend OCR KTP & Form.

Lokal   : uvicorn app.main:app --reload --port 8000   (dari folder backend/)
Cloud Run: lihat DEPLOY-CLOUDRUN.md
"""
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import auth, config, db, exporters
from .ocr_engine import MIME_BY_EXT, OcrError, engine_name, process_file
from .storage import get_storage, new_object_name, valid_object_name

app = FastAPI(title="OCR KTP & Form", version="1.1.0",
              docs_url="/docs" if config.ENABLE_DOCS else None, redoc_url=None,
              openapi_url="/openapi.json" if config.ENABLE_DOCS else None)
if config.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
db.init_db()

FRONTEND_DIR = config.BASE_DIR / "frontend"
DOC_TYPES = {"auto", "ktp", "form"}
PUBLIC_API = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/me"}


# ------------------------------------------------------------------ login
BLOCKED_HTML = """<!doctype html><html lang="id"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Akses ditolak</title>
<body style="font-family:system-ui,sans-serif;background:#F3F6FA;color:#16202E;display:grid;place-items:center;min-height:100vh;margin:0">
<div style="background:#fff;border:1px solid #DCE3EC;border-radius:12px;padding:28px;max-width:420px">
<h1 style="font-size:20px;margin:0 0 8px">Hanya bisa diakses dari jaringan kantor</h1>
<p style="color:#5B6779;margin:0">Sambungkan perangkat ke jaringan kantor lalu muat ulang halaman ini.</p>
<p style="color:#8A94A4;font-size:13px;margin:16px 0 0">IP terdeteksi: {ip}</p></div></body></html>"""


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    ok, ip = auth.ip_allowed(request)
    if not ok:
        if path.startswith("/api/"):
            return JSONResponse({"detail": f"Hanya bisa diakses dari jaringan kantor (IP terdeteksi: {ip})."},
                                status_code=403)
        return Response(BLOCKED_HTML.replace("{ip}", ip or "?"), status_code=403, media_type="text/html")
    if path.startswith("/api/") and path not in PUBLIC_API and auth.mode() != "none":
        user = auth.current_user(request)
        if not user:
            return JSONResponse({"detail": "Silakan masuk terlebih dahulu."}, status_code=401)
        request.state.user = user
    else:
        request.state.user = auth.current_user(request)
    response = await call_next(request)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/auth/login")
def login(request: Request, payload: dict = Body(...)):
    if not auth.enabled():
        return {"user": "operator", "auth_required": False}
    ip = auth.client_ip(request)
    if auth.too_many_attempts(ip):
        raise HTTPException(429, "Terlalu banyak percobaan masuk. Coba lagi 15 menit lagi.")
    username = (payload.get("username") or "").strip()
    if not auth.check_password(username, payload.get("password") or "", ip):
        raise HTTPException(401, "Nama pengguna atau kata sandi salah.")
    resp = JSONResponse({"user": username, "auth_required": True})
    resp.set_cookie(auth.COOKIE, auth.make_token(username), max_age=config.SESSION_HOURS * 3600,
                    httponly=True, samesite="lax", secure=auth.is_https(request), path="/")
    return resp


@app.post("/api/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
def me(request: Request):
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "Belum masuk")
    return {"user": user, "auth_required": auth.enabled(), "auth_mode": auth.mode()}


# ------------------------------------------------------------------ helper upload
def _check_name_size(filename: str, size: Optional[int]):
    ext = Path(filename or "").suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(415, f"Format {ext or 'tanpa ekstensi'} tidak didukung. Gunakan JPG, PNG, TIFF, BMP, atau PDF.")
    if size is not None and size > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Ukuran file melebihi {config.MAX_UPLOAD_MB} MB.")
    if size == 0:
        raise HTTPException(400, "File kosong.")
    return ext


async def _save_multipart(file: UploadFile) -> tuple:
    ext = _check_name_size(file.filename, None)
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    fd, tmp = tempfile.mkstemp(suffix=ext)
    size = 0
    with os.fdopen(fd, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                os.unlink(tmp)
                raise HTTPException(413, f"Ukuran file melebihi {config.MAX_UPLOAD_MB} MB.")
            out.write(chunk)
    if size == 0:
        os.unlink(tmp)
        raise HTTPException(400, "File kosong.")
    name = new_object_name(ext)
    await run_in_threadpool(get_storage().put_file, Path(tmp), name, MIME_BY_EXT[ext])
    return name, MIME_BY_EXT[ext], size


def _check_uploaded_object(object_name: str, filename: str) -> tuple:
    ext = _check_name_size(filename, None)
    if not valid_object_name(object_name) or Path(object_name).suffix.lower() != ext:
        raise HTTPException(400, "Referensi file upload tidak valid.")
    size = get_storage().size(object_name)
    if size is None:
        raise HTTPException(400, "File belum selesai diunggah. Coba unggah ulang.")
    if size > config.MAX_UPLOAD_MB * 1024 * 1024:
        get_storage().delete(object_name)
        raise HTTPException(413, f"Ukuran file melebihi {config.MAX_UPLOAD_MB} MB.")
    return MIME_BY_EXT[ext], size


def _run_scan(doc_id: int, doc_type: str, operator: str = None) -> dict:
    doc = db.get_document(doc_id)
    try:
        content = get_storage().read_bytes(doc["stored_name"])
        result = process_file(content, doc["mime_type"], doc_type, doc["filename"])
    except OcrError as e:
        result = {"status": "failed", "doc_type": doc_type if doc_type != "auto" else doc["doc_type"],
                  "error": str(e), "fields": doc["fields"] if doc["status"] == "success" else {}, "warnings": []}
    except Exception as e:  # error tak terduga tetap dicatat sebagai gagal
        result = {"status": "failed", "doc_type": doc["doc_type"], "error": f"Kesalahan sistem: {e}", "warnings": []}
    if result["doc_type"] == "auto":
        result["doc_type"] = "ktp"
    return db.save_scan_result(doc_id, result, engine_name(), operator=operator)


def _with_flags(doc: dict) -> dict:
    if doc:
        doc["low_quality"] = doc["status"] == "success" and (doc.get("confidence") or 0) < config.LOW_CONFIDENCE_THRESHOLD
    return doc


def _get_or_404(doc_id: int) -> dict:
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Dokumen tidak ditemukan")
    return doc


# ------------------------------------------------------------------ API
@app.get("/api/health")
def health():
    return {"ok": True, "mode": engine_name(), "max_upload_mb": config.MAX_UPLOAD_MB,
            "allowed": sorted(config.ALLOWED_EXTENSIONS), "low_confidence_threshold": config.LOW_CONFIDENCE_THRESHOLD,
            "upload_mode": "signed_url" if config.STORAGE_BACKEND == "gcs" else "multipart",
            "auth_required": auth.enabled(), "auth_mode": auth.mode()}


@app.post("/api/uploads/init")
def upload_init(payload: dict = Body(...)):
    """Mode gcs: buat signed URL agar browser mengunggah langsung ke Cloud Storage."""
    filename, size = payload.get("filename") or "", payload.get("size")
    ext = _check_name_size(filename, size)
    name = new_object_name(ext)
    url = get_storage().signed_upload_url(name, MIME_BY_EXT[ext])
    if not url:
        return {"mode": "multipart"}
    return {"mode": "signed_url", "upload_url": url, "object_name": name, "content_type": MIME_BY_EXT[ext]}


@app.post("/api/scan")
async def scan(request: Request, file: UploadFile = File(...), doc_type: str = Form("auto")):
    if doc_type not in DOC_TYPES:
        raise HTTPException(400, "doc_type harus auto, ktp, atau form")
    name, mime, size = await _save_multipart(file)
    doc_id = db.create_document(file.filename, name, mime, size, "ktp" if doc_type == "auto" else doc_type,
                                created_by=request.state.user)
    return _with_flags(await run_in_threadpool(_run_scan, doc_id, doc_type, request.state.user))


@app.post("/api/scan/object")
async def scan_object(request: Request, payload: dict = Body(...)):
    """Scan file yang sudah diunggah langsung ke Cloud Storage lewat signed URL."""
    doc_type = payload.get("doc_type") or "auto"
    if doc_type not in DOC_TYPES:
        raise HTTPException(400, "doc_type harus auto, ktp, atau form")
    filename, object_name = payload.get("filename") or "", payload.get("object_name") or ""
    mime, size = await run_in_threadpool(_check_uploaded_object, object_name, filename)
    doc_id = db.create_document(filename, object_name, mime, size, "ktp" if doc_type == "auto" else doc_type,
                                created_by=request.state.user)
    return _with_flags(await run_in_threadpool(_run_scan, doc_id, doc_type, request.state.user))


@app.post("/api/documents/{doc_id}/retake")
async def retake(request: Request, doc_id: int, file: Optional[UploadFile] = File(None), doc_type: str = Form("auto")):
    doc = _get_or_404(doc_id)
    if file is not None and file.filename:
        name, mime, size = await _save_multipart(file)
        get_storage().delete(doc["stored_name"])
        db.update_file(doc_id, file.filename, name, mime, size)
    return _with_flags(await run_in_threadpool(
        _run_scan, doc_id, doc_type if doc_type in DOC_TYPES else "auto", request.state.user))


@app.post("/api/documents/{doc_id}/retake/object")
async def retake_object(request: Request, doc_id: int, payload: dict = Body(...)):
    doc = _get_or_404(doc_id)
    filename, object_name = payload.get("filename") or "", payload.get("object_name") or ""
    mime, size = await run_in_threadpool(_check_uploaded_object, object_name, filename)
    get_storage().delete(doc["stored_name"])
    db.update_file(doc_id, filename, object_name, mime, size)
    doc_type = payload.get("doc_type") or "auto"
    return _with_flags(await run_in_threadpool(
        _run_scan, doc_id, doc_type if doc_type in DOC_TYPES else "auto", request.state.user))


@app.get("/api/documents")
def documents(status: Optional[str] = None, doc_type: Optional[str] = None, q: Optional[str] = None,
              date_from: Optional[str] = None, date_to: Optional[str] = None,
              page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200)):
    total, items = db.list_documents(status, doc_type, q, date_from, date_to, limit=page_size,
                                     offset=(page - 1) * page_size, low_threshold=config.LOW_CONFIDENCE_THRESHOLD)
    return {"total": total, "page": page, "page_size": page_size, "items": [_with_flags(d) for d in items]}


@app.get("/api/documents/{doc_id}")
def document(doc_id: int):
    return _with_flags(_get_or_404(doc_id))


@app.put("/api/documents/{doc_id}")
def update_document(doc_id: int, payload: dict = Body(...)):
    _get_or_404(doc_id)
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
    get_storage().delete(doc["stored_name"])
    return {"deleted": doc_id}


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: int):
    doc = _get_or_404(doc_id)
    resp = get_storage().file_response(doc["stored_name"], doc["mime_type"], doc["filename"])
    if resp is None:
        raise HTTPException(404, "File asli sudah tidak tersedia")
    return resp


@app.get("/api/stats")
def stats(days: int = Query(30, ge=7, le=366)):
    s = db.get_stats(days=days, low_threshold=config.LOW_CONFIDENCE_THRESHOLD)
    s["needs_attention"] = [_with_flags(d) for d in s["needs_attention"]]
    s["mode"] = engine_name()
    return s


def _export_docs(ids: Optional[str], status, doc_type, q, date_from, date_to):
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()] if ids else None
    _, docs = db.list_documents(status, doc_type, q, date_from, date_to, ids=id_list, limit=100000,
                                low_threshold=config.LOW_CONFIDENCE_THRESHOLD)
    return docs


@app.get("/api/export")
def export(format: str = Query("xlsx", pattern="^(csv|xlsx|json|sql)$"), ids: Optional[str] = None,
           status: Optional[str] = None, doc_type: Optional[str] = None, q: Optional[str] = None,
           date_from: Optional[str] = None, date_to: Optional[str] = None):
    docs = _export_docs(ids, status, doc_type, q, date_from, date_to)
    if not docs:
        raise HTTPException(404, "Tidak ada dokumen untuk diekspor")
    stamp = db.now_local().strftime("%Y%m%d-%H%M")
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
