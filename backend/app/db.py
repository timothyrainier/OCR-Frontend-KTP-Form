"""Penyimpanan dokumen & log setiap percobaan scan (SQLite)."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    stored_name     TEXT NOT NULL,
    mime_type       TEXT,
    file_size       INTEGER,
    doc_type        TEXT NOT NULL,            -- ktp | form
    status          TEXT NOT NULL,            -- processing | success | failed
    confidence      REAL,
    fields_json     TEXT,                     -- hasil terstruktur
    raw_text        TEXT,
    warnings_json   TEXT,
    error_message   TEXT,
    attempts        INTEGER DEFAULT 0,        -- jumlah percobaan scan (1 = tanpa retake)
    edited          INTEGER DEFAULT 0,        -- 1 jika sudah dikoreksi manual
    verified        INTEGER DEFAULT 0,        -- 1 jika sudah ditandai benar
    processing_ms   INTEGER,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    attempt_no    INTEGER NOT NULL,
    is_retake     INTEGER NOT NULL DEFAULT 0,
    doc_type      TEXT,
    status        TEXT NOT NULL,              -- success | failed
    confidence    REAL,
    error_message TEXT,
    duration_ms   INTEGER,
    engine        TEXT,                       -- mock | documentai
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_created ON scan_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_docs_created ON documents(created_at);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as c:
        c.executescript(SCHEMA)


def row_to_doc(row: sqlite3.Row) -> dict:
    if row is None:
        return None
    d = dict(row)
    d["fields"] = json.loads(d.pop("fields_json") or "{}")
    d["warnings"] = json.loads(d.pop("warnings_json") or "[]")
    d["edited"] = bool(d["edited"])
    d["verified"] = bool(d["verified"])
    return d


# ---------------------------------------------------------------- dokumen
def create_document(filename, stored_name, mime_type, file_size, doc_type, created_at=None) -> int:
    ts = created_at or now_iso()
    with get_conn() as c:
        cur = c.execute(
            """INSERT INTO documents (filename, stored_name, mime_type, file_size, doc_type,
               status, attempts, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'processing', 0, ?, ?)""",
            (filename, stored_name, mime_type, file_size, doc_type, ts, ts),
        )
        return cur.lastrowid


def get_document(doc_id: int) -> dict:
    with get_conn() as c:
        return row_to_doc(c.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone())


def save_scan_result(doc_id: int, result: dict, engine: str, ts: str = None) -> dict:
    """Simpan hasil 1 percobaan scan ke dokumen + catat di scan_logs."""
    ts = ts or now_iso()
    with get_conn() as c:
        doc = c.execute("SELECT attempts FROM documents WHERE id=?", (doc_id,)).fetchone()
        attempt_no = (doc["attempts"] or 0) + 1
        c.execute(
            """UPDATE documents SET status=?, doc_type=?, confidence=?, fields_json=?, raw_text=?,
               warnings_json=?, error_message=?, attempts=?, edited=0, verified=0,
               processing_ms=?, updated_at=? WHERE id=?""",
            (
                result["status"], result["doc_type"], result.get("confidence"),
                json.dumps(result.get("fields") or {}, ensure_ascii=False),
                result.get("raw_text"),
                json.dumps(result.get("warnings") or [], ensure_ascii=False),
                result.get("error"), attempt_no, result.get("duration_ms"), ts, doc_id,
            ),
        )
        c.execute(
            """INSERT INTO scan_logs (document_id, attempt_no, is_retake, doc_type, status,
               confidence, error_message, duration_ms, engine, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, attempt_no, int(attempt_no > 1), result["doc_type"], result["status"],
             result.get("confidence"), result.get("error"), result.get("duration_ms"), engine, ts),
        )
    return get_document(doc_id)


def update_fields(doc_id: int, fields: dict, verified: bool = None) -> dict:
    with get_conn() as c:
        sets, params = ["fields_json=?", "edited=1", "updated_at=?"], [json.dumps(fields, ensure_ascii=False), now_iso()]
        if verified is not None:
            sets.append("verified=?")
            params.append(int(verified))
        params.append(doc_id)
        c.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id=?", params)
    return get_document(doc_id)


def update_file(doc_id: int, filename, stored_name, mime_type, file_size):
    with get_conn() as c:
        c.execute(
            "UPDATE documents SET filename=?, stored_name=?, mime_type=?, file_size=?, updated_at=? WHERE id=?",
            (filename, stored_name, mime_type, file_size, now_iso(), doc_id),
        )


def delete_document(doc_id: int) -> dict:
    doc = get_document(doc_id)
    with get_conn() as c:
        c.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    return doc


def list_documents(status=None, doc_type=None, q=None, date_from=None, date_to=None,
                   ids=None, limit=50, offset=0):
    where, params = [], []
    if status:
        if status == "low":
            from .config import LOW_CONFIDENCE_THRESHOLD
            where.append("status='success' AND confidence < ?")
            params.append(LOW_CONFIDENCE_THRESHOLD)
        else:
            where.append("status=?")
            params.append(status)
    if doc_type:
        where.append("doc_type=?")
        params.append(doc_type)
    if q:
        where.append("(filename LIKE ? OR fields_json LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if date_from:
        where.append("substr(created_at,1,10) >= ?")
        params.append(date_from)
    if date_to:
        where.append("substr(created_at,1,10) <= ?")
        params.append(date_to)
    if ids:
        where.append(f"id IN ({','.join('?' * len(ids))})")
        params += list(ids)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with get_conn() as c:
        total = c.execute(f"SELECT COUNT(*) FROM documents {clause}", params).fetchone()[0]
        rows = c.execute(
            f"SELECT * FROM documents {clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return total, [row_to_doc(r) for r in rows]


# ---------------------------------------------------------------- statistik
def get_stats(days: int = 30, months: int = 12, low_threshold: float = 0.8) -> dict:
    today = datetime.now().date()
    month_prefix = today.strftime("%Y-%m")
    with get_conn() as c:
        q = lambda sql, p=(): c.execute(sql, p).fetchone()  # noqa: E731

        docs = q("""SELECT COUNT(*) total,
                    SUM(status='success') success, SUM(status='failed') failed,
                    SUM(status='success' AND confidence < ?) low,
                    SUM(edited) edited, SUM(verified) verified,
                    AVG(CASE WHEN status='success' THEN confidence END) avg_conf
                    FROM documents""", (low_threshold,))
        logs = q("""SELECT COUNT(*) scans, SUM(status='success') success, SUM(status='failed') failed,
                    SUM(is_retake) retakes, AVG(duration_ms) avg_ms FROM scan_logs""")
        today_row = q("""SELECT COUNT(*) scans, SUM(status='success') success, SUM(status='failed') failed
                         FROM scan_logs WHERE substr(created_at,1,10)=?""", (today.isoformat(),))
        today_docs = q("SELECT COUNT(*) FROM documents WHERE substr(created_at,1,10)=?", (today.isoformat(),))[0]
        month_row = q("""SELECT COUNT(*) scans, SUM(status='success') success, SUM(status='failed') failed
                         FROM scan_logs WHERE substr(created_at,1,7)=?""", (month_prefix,))
        month_docs = q("SELECT COUNT(*) FROM documents WHERE substr(created_at,1,7)=?", (month_prefix,))[0]

        start = today - timedelta(days=days - 1)
        daily_rows = c.execute(
            """SELECT substr(created_at,1,10) d, SUM(status='success') s, SUM(status='failed') f
               FROM scan_logs WHERE substr(created_at,1,10) >= ? GROUP BY d""", (start.isoformat(),)
        ).fetchall()
        daily_map = {r["d"]: r for r in daily_rows}
        daily = []
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            r = daily_map.get(d)
            daily.append({"date": d, "success": (r["s"] or 0) if r else 0, "failed": (r["f"] or 0) if r else 0})

        monthly_rows = c.execute(
            """SELECT substr(created_at,1,7) m, SUM(status='success') s, SUM(status='failed') f
               FROM scan_logs GROUP BY m""").fetchall()
        monthly_map = {r["m"]: r for r in monthly_rows}
        monthly, y, m = [], today.year, today.month
        for _ in range(months):
            key = f"{y:04d}-{m:02d}"
            r = monthly_map.get(key)
            monthly.append({"month": key, "success": (r["s"] or 0) if r else 0, "failed": (r["f"] or 0) if r else 0})
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        monthly.reverse()

        by_type = {r["doc_type"]: r["n"] for r in c.execute(
            "SELECT doc_type, COUNT(*) n FROM documents GROUP BY doc_type").fetchall()}
        errors = [dict(r) for r in c.execute(
            """SELECT error_message, COUNT(*) n FROM scan_logs WHERE status='failed'
               GROUP BY error_message ORDER BY n DESC LIMIT 5""").fetchall()]
        attention = [row_to_doc(r) for r in c.execute(
            """SELECT * FROM documents WHERE status='failed' OR (status='success' AND confidence < ?)
               ORDER BY updated_at DESC LIMIT 8""", (low_threshold,)).fetchall()]

    scans = logs["scans"] or 0
    return {
        "documents": {
            "total": docs["total"] or 0, "success": docs["success"] or 0, "failed": docs["failed"] or 0,
            "low_quality": docs["low"] or 0, "edited": docs["edited"] or 0, "verified": docs["verified"] or 0,
            "avg_confidence": round(docs["avg_conf"], 4) if docs["avg_conf"] is not None else None,
        },
        "scans": {
            "total": scans, "success": logs["success"] or 0, "failed": logs["failed"] or 0,
            "retakes": logs["retakes"] or 0,
            "success_rate": round((logs["success"] or 0) / scans, 4) if scans else None,
            "avg_duration_ms": int(logs["avg_ms"]) if logs["avg_ms"] else None,
        },
        "today": {"documents": today_docs, "scans": today_row["scans"] or 0,
                  "success": today_row["success"] or 0, "failed": today_row["failed"] or 0},
        "this_month": {"documents": month_docs, "scans": month_row["scans"] or 0,
                       "success": month_row["success"] or 0, "failed": month_row["failed"] or 0},
        "daily": daily,
        "monthly": monthly,
        "by_type": {"ktp": by_type.get("ktp", 0), "form": by_type.get("form", 0)},
        "top_errors": errors,
        "needs_attention": attention,
    }
