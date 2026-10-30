"""Penyimpanan dokumen & log setiap percobaan scan.

Memakai SQLAlchemy Core sehingga sama-sama jalan di SQLite (lokal) dan PostgreSQL (Cloud Run).
Waktu disimpan sebagai teks ISO lokal (APP_TIMEZONE) agar rekap harian sesuai jam WIB.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import (Column, Float, Integer, MetaData, String, Table, Text, create_engine, delete, func,
                        insert, select, text, update)

from .config import APP_TIMEZONE, DATABASE_URL

_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
else:
    _kwargs.update(pool_size=5, max_overflow=5, pool_recycle=1800)
engine = create_engine(DATABASE_URL, **_kwargs)

if DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        # WAL wajib untuk Litestream (replikasi SQLite ke Cloud Storage) dan lebih aman untuk akses bersamaan
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
TZ = ZoneInfo(APP_TIMEZONE)

md = MetaData()
documents = Table(
    "documents", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("filename", String(255), nullable=False),
    Column("stored_name", String(255), nullable=False),
    Column("mime_type", String(100)),
    Column("file_size", Integer),
    Column("doc_type", String(10), nullable=False),          # ktp | form
    Column("status", String(12), nullable=False),            # processing | success | failed
    Column("confidence", Float),
    Column("fields_json", Text),
    Column("raw_text", Text),
    Column("warnings_json", Text),
    Column("error_message", Text),
    Column("attempts", Integer, default=0),
    Column("edited", Integer, default=0),
    Column("verified", Integer, default=0),
    Column("processing_ms", Integer),
    Column("created_by", String(100)),
    Column("created_at", String(19), nullable=False, index=True),
    Column("updated_at", String(19), nullable=False),
)
scan_logs = Table(
    "scan_logs", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("document_id", Integer, nullable=False, index=True),
    Column("attempt_no", Integer, nullable=False),
    Column("is_retake", Integer, nullable=False, default=0),
    Column("doc_type", String(10)),
    Column("status", String(12), nullable=False),
    Column("confidence", Float),
    Column("error_message", Text),
    Column("duration_ms", Integer),
    Column("engine", String(20)),
    Column("operator", String(100)),
    Column("created_at", String(19), nullable=False, index=True),
)


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def now_iso() -> str:
    return now_local().isoformat(timespec="seconds")


def init_db():
    md.create_all(engine)


def row_to_doc(row) -> dict:
    if row is None:
        return None
    d = dict(row._mapping)
    d["fields"] = json.loads(d.pop("fields_json") or "{}")
    d["warnings"] = json.loads(d.pop("warnings_json") or "[]")
    d["edited"] = bool(d["edited"])
    d["verified"] = bool(d["verified"])
    return d


# ---------------------------------------------------------------- dokumen
def create_document(filename, stored_name, mime_type, file_size, doc_type, created_at=None, created_by=None) -> int:
    ts = created_at or now_iso()
    with engine.begin() as c:
        res = c.execute(insert(documents).values(
            filename=filename, stored_name=stored_name, mime_type=mime_type, file_size=file_size,
            doc_type=doc_type, status="processing", attempts=0, edited=0, verified=0,
            created_by=created_by, created_at=ts, updated_at=ts))
        return res.inserted_primary_key[0]


def get_document(doc_id: int) -> dict:
    with engine.connect() as c:
        return row_to_doc(c.execute(select(documents).where(documents.c.id == doc_id)).first())


def save_scan_result(doc_id: int, result: dict, engine_name: str, ts: str = None, operator: str = None) -> dict:
    """Simpan hasil 1 percobaan scan ke dokumen + catat di scan_logs."""
    ts = ts or now_iso()
    with engine.begin() as c:
        attempts = c.execute(select(documents.c.attempts).where(documents.c.id == doc_id)).scalar() or 0
        attempt_no = attempts + 1
        c.execute(update(documents).where(documents.c.id == doc_id).values(
            status=result["status"], doc_type=result["doc_type"], confidence=result.get("confidence"),
            fields_json=json.dumps(result.get("fields") or {}, ensure_ascii=False),
            raw_text=result.get("raw_text"),
            warnings_json=json.dumps(result.get("warnings") or [], ensure_ascii=False),
            error_message=result.get("error"), attempts=attempt_no, edited=0, verified=0,
            processing_ms=result.get("duration_ms"), updated_at=ts))
        c.execute(insert(scan_logs).values(
            document_id=doc_id, attempt_no=attempt_no, is_retake=int(attempt_no > 1), doc_type=result["doc_type"],
            status=result["status"], confidence=result.get("confidence"), error_message=result.get("error"),
            duration_ms=result.get("duration_ms"), engine=engine_name, operator=operator, created_at=ts))
    return get_document(doc_id)


def update_fields(doc_id: int, fields: dict, verified: bool = None) -> dict:
    vals = {"fields_json": json.dumps(fields, ensure_ascii=False), "edited": 1, "updated_at": now_iso()}
    if verified is not None:
        vals["verified"] = int(verified)
    with engine.begin() as c:
        c.execute(update(documents).where(documents.c.id == doc_id).values(**vals))
    return get_document(doc_id)


def update_file(doc_id: int, filename, stored_name, mime_type, file_size):
    with engine.begin() as c:
        c.execute(update(documents).where(documents.c.id == doc_id).values(
            filename=filename, stored_name=stored_name, mime_type=mime_type, file_size=file_size,
            updated_at=now_iso()))


def delete_document(doc_id: int) -> dict:
    doc = get_document(doc_id)
    if doc:
        with engine.begin() as c:
            c.execute(delete(scan_logs).where(scan_logs.c.document_id == doc_id))
            c.execute(delete(documents).where(documents.c.id == doc_id))
    return doc


def list_documents(status=None, doc_type=None, q=None, date_from=None, date_to=None,
                   ids=None, limit=50, offset=0, low_threshold=0.8):
    conds = []
    t = documents.c
    if status == "low":
        conds += [t.status == "success", t.confidence < low_threshold]
    elif status:
        conds.append(t.status == status)
    if doc_type:
        conds.append(t.doc_type == doc_type)
    if q:
        like = f"%{q}%"
        conds.append(t.filename.ilike(like) | t.fields_json.ilike(like))
    if date_from:
        conds.append(t.created_at >= date_from)
    if date_to:
        conds.append(t.created_at < f"{date_to}T99")      # akhir hari (perbandingan teks ISO)
    if ids:
        conds.append(t.id.in_(list(ids)))
    with engine.connect() as c:
        total = c.execute(select(func.count()).select_from(documents).where(*conds)).scalar()
        rows = c.execute(select(documents).where(*conds)
                         .order_by(t.created_at.desc(), t.id.desc()).limit(limit).offset(offset)).all()
    return total, [row_to_doc(r) for r in rows]


# ---------------------------------------------------------------- statistik
def _n(v):
    return int(v or 0)


def get_stats(days: int = 30, months: int = 12, low_threshold: float = 0.8) -> dict:
    today = now_local().date()
    day, month_prefix = today.isoformat(), today.strftime("%Y-%m")
    start = today - timedelta(days=days - 1)
    ok, bad = "CASE WHEN status='success' THEN 1 ELSE 0 END", "CASE WHEN status='failed' THEN 1 ELSE 0 END"
    with engine.connect() as c:
        q = lambda sql, **p: c.execute(text(sql), p).mappings().first()  # noqa: E731
        docs = q(f"""SELECT COUNT(*) total, SUM({ok}) success, SUM({bad}) failed,
                     SUM(CASE WHEN status='success' AND confidence < :t THEN 1 ELSE 0 END) low,
                     SUM(edited) edited, SUM(verified) verified,
                     AVG(CASE WHEN status='success' THEN confidence END) avg_conf FROM documents""", t=low_threshold)
        logs = q(f"""SELECT COUNT(*) scans, SUM({ok}) success, SUM({bad}) failed,
                     SUM(is_retake) retakes, AVG(duration_ms) avg_ms FROM scan_logs""")
        def period(pfx):
            return q(f"""SELECT COUNT(*) scans, SUM({ok}) success, SUM({bad}) failed
                         FROM scan_logs WHERE substr(created_at,1,{len(pfx)}) = :p""", p=pfx)
        today_row, month_row = period(day), period(month_prefix)
        cnt_docs = lambda pfx: c.execute(text(  # noqa: E731
            f"SELECT COUNT(*) FROM documents WHERE substr(created_at,1,{len(pfx)}) = :p"), {"p": pfx}).scalar()

        daily_map = {r["d"]: r for r in c.execute(text(f"""
            SELECT substr(created_at,1,10) d, SUM({ok}) s, SUM({bad}) f FROM scan_logs
            WHERE created_at >= :st GROUP BY substr(created_at,1,10)"""), {"st": start.isoformat()}).mappings()}
        daily = []
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            r = daily_map.get(d)
            daily.append({"date": d, "success": _n(r and r["s"]), "failed": _n(r and r["f"])})

        monthly_map = {r["m"]: r for r in c.execute(text(f"""
            SELECT substr(created_at,1,7) m, SUM({ok}) s, SUM({bad}) f FROM scan_logs
            GROUP BY substr(created_at,1,7)""")).mappings()}
        monthly, y, m = [], today.year, today.month
        for _ in range(months):
            key = f"{y:04d}-{m:02d}"
            r = monthly_map.get(key)
            monthly.append({"month": key, "success": _n(r and r["s"]), "failed": _n(r and r["f"])})
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        monthly.reverse()

        by_type = {r["doc_type"]: r["n"] for r in c.execute(text(
            "SELECT doc_type, COUNT(*) n FROM documents GROUP BY doc_type")).mappings()}
        errors = [dict(r) for r in c.execute(text(
            """SELECT error_message, COUNT(*) n FROM scan_logs WHERE status='failed'
               GROUP BY error_message ORDER BY COUNT(*) DESC LIMIT 5""")).mappings()]
        attention = [row_to_doc(r) for r in c.execute(
            select(documents).where((documents.c.status == "failed") |
                                    ((documents.c.status == "success") & (documents.c.confidence < low_threshold)))
            .order_by(documents.c.updated_at.desc()).limit(8)).all()]
        today_docs, month_docs = cnt_docs(day), cnt_docs(month_prefix)

    scans = _n(logs["scans"])
    return {
        "documents": {
            "total": _n(docs["total"]), "success": _n(docs["success"]), "failed": _n(docs["failed"]),
            "low_quality": _n(docs["low"]), "edited": _n(docs["edited"]), "verified": _n(docs["verified"]),
            "avg_confidence": round(float(docs["avg_conf"]), 4) if docs["avg_conf"] is not None else None,
        },
        "scans": {
            "total": scans, "success": _n(logs["success"]), "failed": _n(logs["failed"]),
            "retakes": _n(logs["retakes"]),
            "success_rate": round(_n(logs["success"]) / scans, 4) if scans else None,
            "avg_duration_ms": int(logs["avg_ms"]) if logs["avg_ms"] else None,
        },
        "today": {"documents": _n(today_docs), "scans": _n(today_row["scans"]),
                  "success": _n(today_row["success"]), "failed": _n(today_row["failed"])},
        "this_month": {"documents": _n(month_docs), "scans": _n(month_row["scans"]),
                       "success": _n(month_row["success"]), "failed": _n(month_row["failed"])},
        "daily": daily, "monthly": monthly,
        "by_type": {"ktp": _n(by_type.get("ktp")), "form": _n(by_type.get("form"))},
        "top_errors": [{"error_message": e["error_message"], "n": _n(e["n"])} for e in errors],
        "needs_attention": attention,
    }
