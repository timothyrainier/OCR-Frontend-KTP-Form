"""Penyimpanan file upload.

- local : folder data/uploads (untuk dijalankan di komputer sendiri)
- gcs   : Google Cloud Storage (untuk Cloud Run, karena disk Cloud Run tidak permanen)

Di mode gcs, browser mengunggah file langsung ke bucket memakai signed URL. Ini menghindari
batas ukuran request Cloud Run (32 MB) sehingga file sampai MAX_UPLOAD_MB tetap bisa diunggah.
"""
import re
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse

from . import config
from .db import now_local

OBJECT_RE = re.compile(r"^uploads/\d{4}/\d{2}/[0-9a-f]{32}\.(jpg|jpeg|png|tif|tiff|bmp|pdf)$")


def new_object_name(ext: str) -> str:
    n = now_local()
    return f"uploads/{n:%Y}/{n:%m}/{uuid.uuid4().hex}{ext}"


def valid_object_name(name: str) -> bool:
    return bool(OBJECT_RE.match(name or ""))


class LocalStorage:
    kind = "local"

    def _path(self, name: str) -> Path:
        p = (config.UPLOAD_DIR / name).resolve()
        if config.UPLOAD_DIR.resolve() not in p.parents:
            raise ValueError("Nama file tidak valid")
        return p

    def put_file(self, src: Path, name: str, content_type: str):
        dest = self._path(name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), dest)

    def read_bytes(self, name: str) -> bytes:
        return self._path(name).read_bytes()

    def size(self, name: str):
        p = self._path(name)
        return p.stat().st_size if p.exists() else None

    def delete(self, name: str):
        try:
            self._path(name).unlink(missing_ok=True)
        except ValueError:
            pass

    def file_response(self, name: str, mime: str, filename: str):
        p = self._path(name)
        if not p.exists():
            return None
        return FileResponse(p, media_type=mime, filename=filename, content_disposition_type="inline")

    def signed_upload_url(self, name: str, content_type: str):
        return None  # mode lokal memakai upload multipart biasa


class GcsStorage:
    kind = "gcs"

    def __init__(self, bucket: str):
        from google.cloud import storage
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket)

    def _sign_kwargs(self):
        """Di Cloud Run tidak ada private key; tanda tangan dibuat lewat IAM signBlob
        (service account perlu peran Service Account Token Creator pada dirinya sendiri)."""
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        creds = self.client._credentials
        if isinstance(creds, service_account.Credentials):
            return {}
        if not creds.valid:
            creds.refresh(Request())
        return {"service_account_email": creds.service_account_email, "access_token": creds.token}

    def put_file(self, src: Path, name: str, content_type: str):
        self.bucket.blob(name).upload_from_filename(str(src), content_type=content_type)
        Path(src).unlink(missing_ok=True)

    def read_bytes(self, name: str) -> bytes:
        return self.bucket.blob(name).download_as_bytes()

    def size(self, name: str):
        blob = self.bucket.get_blob(name)
        return blob.size if blob else None

    def delete(self, name: str):
        try:
            self.bucket.blob(name).delete()
        except Exception:
            pass

    def file_response(self, name: str, mime: str, filename: str):
        blob = self.bucket.blob(name)
        url = blob.generate_signed_url(
            version="v4", expiration=timedelta(minutes=15), method="GET",
            response_type=mime, response_disposition=f'inline; filename="{filename.replace(chr(34), "")}"',
            **self._sign_kwargs())
        return RedirectResponse(url, status_code=307)

    def signed_upload_url(self, name: str, content_type: str):
        return self.bucket.blob(name).generate_signed_url(
            version="v4", expiration=timedelta(minutes=30), method="PUT", content_type=content_type,
            **self._sign_kwargs())


_storage = None


def get_storage():
    global _storage
    if _storage is None:
        if config.STORAGE_BACKEND == "gcs":
            if not config.GCS_BUCKET:
                raise RuntimeError("STORAGE_BACKEND=gcs tetapi GCS_BUCKET belum diisi")
            _storage = GcsStorage(config.GCS_BUCKET)
        else:
            _storage = LocalStorage()
    return _storage
