"""Login operator sederhana: daftar user dari AUTH_USERS, sesi disimpan di cookie bertanda tangan HMAC."""
import base64
import ipaddress
import logging
import hashlib
import hmac
import time
from collections import defaultdict

from fastapi import Request

from . import config

COOKIE = "pindai_session"
_failed = defaultdict(list)          # ip -> waktu percobaan gagal
MAX_FAILED, WINDOW = 8, 15 * 60


log = logging.getLogger("pindai.access")
_networks = [ipaddress.ip_network(n, strict=False) for n in config.OFFICE_IPS]


def mode() -> str:
    if config.AUTH_MODE == "iap":
        return "iap"
    return "password" if config.AUTH_USERS else "none"


def enabled() -> bool:
    """True bila aplikasi sendiri yang menampilkan form login (mode password)."""
    return mode() == "password"


def iap_user(request: Request):
    # Format header: "accounts.google.com:nama@domain.com". Aman dipercaya karena layanan
    # Cloud Run tidak bisa dipanggil langsung tanpa melewati IAP (tanpa allUsers).
    v = request.headers.get("x-goog-authenticated-user-email", "")
    return v.split(":", 1)[-1] or None


def _sign(payload: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(username: str) -> str:
    exp = int(time.time()) + config.SESSION_HOURS * 3600
    payload = f"{username}|{exp}"
    return base64.urlsafe_b64encode(f"{payload}|{_sign(payload)}".encode()).decode()


def read_token(token: str):
    try:
        username, exp, sig = base64.urlsafe_b64decode(token.encode()).decode().rsplit("|", 2)
    except Exception:
        return None
    if not hmac.compare_digest(sig, _sign(f"{username}|{exp}")) or int(exp) < time.time():
        return None
    return username if username in config.AUTH_USERS else None


def current_user(request: Request):
    m = mode()
    if m == "iap":
        return iap_user(request)
    if m == "none":
        return "operator"
    token = request.cookies.get(COOKIE)
    return read_token(token) if token else None


def client_ip(request: Request) -> str:
    """IP klien sebenarnya. Google menambahkan IP klien di UJUNG X-Forwarded-For, sehingga
    nilai di depan (yang bisa dipalsukan pengirim) diabaikan."""
    parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    idx = len(parts) - 1 - config.TRUSTED_PROXY_HOPS
    if parts and idx >= 0:
        return parts[idx]
    return request.client.host if request.client else "?"


def ip_allowed(request: Request) -> tuple:
    if not _networks:
        return True, None
    ip = client_ip(request)
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        addr = None
    ok = addr is not None and any(addr in n for n in _networks)
    if not ok:
        log.warning("Akses ditolak dari IP %s (X-Forwarded-For=%r)", ip, request.headers.get("x-forwarded-for"))
    return ok, ip


def too_many_attempts(ip: str) -> bool:
    now = time.time()
    _failed[ip] = [t for t in _failed[ip] if now - t < WINDOW]
    return len(_failed[ip]) >= MAX_FAILED


def check_password(username: str, password: str, ip: str) -> bool:
    expected = config.AUTH_USERS.get(username)
    ok = expected is not None and hmac.compare_digest(expected.encode(), (password or "").encode())
    if not ok:
        _failed[ip].append(time.time())
    else:
        _failed.pop(ip, None)
    return ok


def is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
