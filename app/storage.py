"""Storage layer — Phase 10: adds user_id column, sessions table, and startup migration.

Backward compat: owner_token field is preserved in all job payloads.
New code uses user_id for ownership checks. Migration backfills user_id from
owner_token on first startup (zero data loss).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DB = Path(os.getenv("JOB_DB", str(Path(__file__).resolve().parents[1] / "jobs.sqlite3")))
_lock = threading.RLock()
import contextlib


def _token_to_user_id(token: str) -> str:
    """Same derivation as auth.token_to_user_id — kept local to avoid circular import."""
    h = hashlib.sha256(token.encode()).hexdigest()[:16]
    return f"tok:{h}"


@contextlib.contextmanager
def _connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        # Core jobs table
        db.execute(
            "CREATE TABLE IF NOT EXISTS jobs("
            "id TEXT PRIMARY KEY,"
            "payload TEXT NOT NULL,"
            "updated_at REAL NOT NULL DEFAULT (unixepoch()),"
            "user_id TEXT"
            ")"
        )
        # Add user_id column for existing DBs (idempotent)
        try:
            db.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT")
        except sqlite3.OperationalError:
            pass  # already exists

        db.execute(
            "CREATE TABLE IF NOT EXISTS idempotency("
            "key TEXT PRIMARY KEY,"
            "job_id TEXT NOT NULL,"
            "created_at REAL NOT NULL DEFAULT (unixepoch())"
            ")"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS usage_meter("
            "month TEXT PRIMARY KEY,"
            "azure_chars INTEGER DEFAULT 0"
            ")"
        )

        # daily_quota: keyed by (day, user_id)
        # Phase 9 used (day, token) — we keep the same schema but rename semantically.
        # New rows use user_id values; old token-keyed rows remain readable.
        db.execute(
            "CREATE TABLE IF NOT EXISTS daily_quota("
            "day TEXT,"
            "user_id TEXT,"
            "minutes REAL DEFAULT 0.0,"
            "PRIMARY KEY(day, user_id)"
            ")"
        )
        # Handle old schema that had column named 'token' instead of 'user_id'
        try:
            db.execute("ALTER TABLE daily_quota ADD COLUMN user_id TEXT")
        except sqlite3.OperationalError:
            pass

        # Sessions table (Phase 10 — server-side session records)
        db.execute(
            "CREATE TABLE IF NOT EXISTS sessions("
            "session_id TEXT PRIMARY KEY,"
            "user_id TEXT NOT NULL,"
            "auth_mode TEXT NOT NULL,"
            "created_at REAL NOT NULL DEFAULT (unixepoch()),"
            "expires_at REAL NOT NULL,"
            "data TEXT"
            ")"
        )
        try:
            db.execute("ALTER TABLE sessions ADD COLUMN data TEXT")
        except sqlite3.OperationalError:
            pass

        # Indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")

        with db:
            yield db
    finally:
        db.close()


def run_migration() -> None:
    """Backfill user_id on existing jobs from owner_token. Safe to run multiple times."""
    try:
        with _lock, _connect() as db:
            rows = db.execute(
                "SELECT id, payload FROM jobs WHERE user_id IS NULL"
            ).fetchall()
            updated = 0
            for row in rows:
                try:
                    data = json.loads(row["payload"])
                    owner_token = data.get("owner_token")
                    if owner_token:
                        uid = _token_to_user_id(owner_token)
                        data["user_id"] = uid
                        db.execute(
                            "UPDATE jobs SET user_id=?, payload=? WHERE id=?",
                            (uid, json.dumps(data, ensure_ascii=False), row["id"]),
                        )
                        updated += 1
                except Exception:
                    pass
            if updated:
                import logging
                logging.getLogger("dubber.storage").info(
                    "Migration: backfilled user_id on %d jobs", updated
                )
    except Exception as exc:
        import logging
        logging.getLogger("dubber.storage").warning("Migration failed (non-fatal): %s", exc)


def set_job(job_id: str, payload: dict[str, Any]) -> None:
    user_id = payload.get("user_id")
    if not user_id and payload.get("owner_token"):
        user_id = _token_to_user_id(payload["owner_token"])
        payload["user_id"] = user_id
    with _lock, _connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO jobs(id, payload, updated_at, user_id) "
            "VALUES(?, ?, unixepoch(), ?)",
            (job_id, json.dumps(payload, ensure_ascii=False), user_id),
        )


def get_job(
    job_id: str,
    owner_token: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    with _lock, _connect() as db:
        row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    data = json.loads(row["payload"])

    if user_id is not None:
        job_uid = data.get("user_id")
        job_owner_token = data.get("owner_token")
        if job_uid:
            if job_uid != user_id:
                return None
        elif job_owner_token:
            if _token_to_user_id(job_owner_token) != user_id:
                return None
        else:
            return None
    elif owner_token is not None:
        job_owner = data.get("owner_token")
        if job_owner and job_owner != owner_token:
            return None

    return data


def get_idempotent_job(
    key: str,
    owner_token: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    with _lock, _connect() as db:
        row = db.execute("SELECT job_id FROM idempotency WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    return get_job(row["job_id"], owner_token=owner_token, user_id=user_id)


def set_idempotent_job(key: str, job_id: str) -> None:
    with _lock, _connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO idempotency(key, job_id, created_at) VALUES(?, ?, unixepoch())",
            (key, job_id),
        )


def update_job(job_id: str, **updates: Any) -> dict[str, Any]:
    with _lock:
        job = get_job(job_id)
        if job is None:
            raise KeyError(f"Job {job_id} not found")
        if job.get("status") == "canceled" and updates.get("status") != "canceled":
            updates.pop("status", None)
            updates.pop("stage", None)
            updates.pop("progress", None)
        job.update(updates)
        set_job(job_id, job)
        return job


def list_jobs(
    owner_token: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    with _lock, _connect() as db:
        rows = db.execute("SELECT payload, user_id FROM jobs ORDER BY updated_at DESC").fetchall()
    all_jobs = []
    for r in rows:
        p = json.loads(r["payload"])
        uid = r["user_id"] or p.get("user_id")
        if not uid and p.get("owner_token"):
            uid = _token_to_user_id(p["owner_token"])
        p["_effective_user_id"] = uid
        all_jobs.append(p)

    if user_id is not None:
        return [j for j in all_jobs if j.get("_effective_user_id") == user_id]
    if owner_token is not None:
        target_uid = _token_to_user_id(owner_token)
        return [
            j for j in all_jobs
            if j.get("owner_token") == owner_token or j.get("_effective_user_id") == target_uid
        ]
    return all_jobs


def delete_job(job_id: str) -> None:
    with _lock, _connect() as db:
        db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        db.execute("DELETE FROM idempotency WHERE job_id=?", (job_id,))


def recover_jobs() -> None:
    from app.i18n import get_km
    for job in list_jobs():
        if job.get("status") in {"queued", "running"}:
            src = Path(job.get("source_path") or "")
            if src.is_file() and src.stat().st_size > 0:
                try:
                    from app.api import POOL, worker
                    update_job(job["id"], status="queued", stage="resuming")
                    POOL.submit(worker, job["id"], str(src), job.get("options", {}))
                    continue
                except Exception:
                    pass
            update_job(
                job["id"],
                status="failed",
                stage="failed",
                error={
                    "code": "SERVER_RESTARTED",
                    "message_en": "Job interrupted by server restart.",
                    "message_km": get_km("server_restarted"),
                },
            )


# ── Azure metering ────────────────────────────────────────────────────────────

def get_current_month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def get_current_day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def get_monthly_azure_chars(month: str | None = None) -> int:
    m = month or get_current_month()
    with _lock, _connect() as db:
        row = db.execute("SELECT azure_chars FROM usage_meter WHERE month=?", (m,)).fetchone()
    return int(row["azure_chars"]) if row else 0


def record_azure_chars(chars: int, month: str | None = None) -> int:
    m = month or get_current_month()
    with _lock, _connect() as db:
        db.execute(
            "INSERT INTO usage_meter(month, azure_chars) VALUES(?, ?) "
            "ON CONFLICT(month) DO UPDATE SET azure_chars = azure_chars + ?",
            (m, chars, chars),
        )
        row = db.execute("SELECT azure_chars FROM usage_meter WHERE month=?", (m,)).fetchone()
    return int(row["azure_chars"]) if row else chars


# ── Daily quota (keyed by user_id) ────────────────────────────────────────────

def get_daily_quota_used(user_id: str, day: str | None = None) -> float:
    d = day or get_current_day()
    with _lock, _connect() as db:
        row = db.execute(
            "SELECT minutes FROM daily_quota WHERE day=? AND user_id=?", (d, user_id)
        ).fetchone()
    return float(row["minutes"]) if row else 0.0


def add_daily_quota_used(user_id: str, minutes: float, day: str | None = None) -> float:
    d = day or get_current_day()
    with _lock, _connect() as db:
        db.execute(
            "INSERT INTO daily_quota(day, user_id, minutes) VALUES(?, ?, ?) "
            "ON CONFLICT(day, user_id) DO UPDATE SET minutes = minutes + ?",
            (d, user_id, minutes, minutes),
        )
        row = db.execute(
            "SELECT minutes FROM daily_quota WHERE day=? AND user_id=?", (d, user_id)
        ).fetchone()
    return float(row["minutes"]) if row else minutes


def cleanup_expired_jobs(ttl_hours: int = 24) -> int:
    cutoff = time.time() - (ttl_hours * 3600)
    count = 0
    with _lock, _connect() as db:
        rows = db.execute(
            "SELECT id, payload FROM jobs WHERE updated_at < ?", (cutoff,)
        ).fetchall()
        for row in rows:
            jid = row["id"]
            db.execute("DELETE FROM jobs WHERE id=?", (jid,))
            db.execute("DELETE FROM idempotency WHERE job_id=?", (jid,))
            count += 1
    return count


def cleanup_expired_sessions(ttl_hours: int = 24) -> int:
    cutoff = time.time()
    with _lock, _connect() as db:
        result = db.execute("DELETE FROM sessions WHERE expires_at < ?", (cutoff,))
        return result.rowcount if hasattr(result, "rowcount") else 0


def create_session(
    session_id: str,
    user_id: str,
    auth_mode: str,
    expires_at: float,
    data: dict[str, Any] | None = None,
) -> None:
    data_str = json.dumps(data or {}, ensure_ascii=False)
    with _lock, _connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO sessions(session_id, user_id, auth_mode, created_at, expires_at, data) "
            "VALUES(?, ?, ?, unixepoch(), ?, ?)",
            (session_id, user_id, auth_mode, expires_at, data_str),
        )


def get_session(session_id: str) -> dict[str, Any] | None:
    now = time.time()
    with _lock, _connect() as db:
        row = db.execute(
            "SELECT session_id, user_id, auth_mode, created_at, expires_at, data FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        if float(row["expires_at"]) < now:
            db.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            return None
    data_dict = json.loads(row["data"]) if row["data"] else {}
    return {
        "session_id": row["session_id"],
        "user_id": row["user_id"],
        "auth_mode": row["auth_mode"],
        "created_at": float(row["created_at"]),
        "expires_at": float(row["expires_at"]),
        "data": data_dict,
    }


def revoke_session(session_id: str) -> bool:
    with _lock, _connect() as db:
        res = db.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
        return (res.rowcount or 0) > 0
