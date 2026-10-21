from __future__ import annotations
import json, os, sqlite3, threading, time
from pathlib import Path
from typing import Any
DB=Path(os.getenv("JOB_DB",str(Path(__file__).resolve().parents[1]/"jobs.sqlite3")))
_lock=threading.RLock()
import contextlib

@contextlib.contextmanager
def _connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at REAL NOT NULL DEFAULT (unixepoch()))")
        db.execute("CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY,job_id TEXT NOT NULL,created_at REAL NOT NULL DEFAULT (unixepoch()))")
        db.execute("CREATE TABLE IF NOT EXISTS usage_meter(month TEXT PRIMARY KEY, azure_chars INTEGER DEFAULT 0)")
        db.execute("CREATE TABLE IF NOT EXISTS daily_quota(day TEXT, token TEXT, minutes REAL DEFAULT 0.0, PRIMARY KEY(day, token))")
        with db:
            yield db
    finally:
        db.close()

def set_job(job_id:str,payload:dict[str,Any])->None:
 with _lock, _connect() as db:
  db.execute("INSERT OR REPLACE INTO jobs(id,payload,updated_at) VALUES(?,?,unixepoch())",(job_id,json.dumps(payload,ensure_ascii=False)))

def get_job(job_id:str, owner_token:str|None=None)->dict[str,Any]|None:
 with _lock, _connect() as db:
  row=db.execute("SELECT payload FROM jobs WHERE id=?",(job_id,)).fetchone()
 if not row: return None
 data = json.loads(row["payload"])
 if owner_token is not None:
  job_owner = data.get("owner_token")
  if job_owner and job_owner != owner_token:
   return None
 return data

def get_idempotent_job(key:str, owner_token:str|None=None)->dict[str,Any]|None:
 with _lock, _connect() as db:
  row=db.execute("SELECT job_id FROM idempotency WHERE key=?",(key,)).fetchone()
 if not row: return None
 return get_job(row["job_id"], owner_token=owner_token)

def set_idempotent_job(key:str,job_id:str)->None:
 with _lock, _connect() as db:
  db.execute("INSERT OR REPLACE INTO idempotency(key,job_id,created_at) VALUES(?,?,unixepoch())",(key,job_id))

def update_job(job_id:str,**updates:Any)->dict[str,Any]:
 with _lock:
  job=get_job(job_id)
  if job is None: raise KeyError(f"Job {job_id} not found")
  if job.get("status") == "canceled" and updates.get("status") != "canceled":
   updates.pop("status", None); updates.pop("stage", None); updates.pop("progress", None)
  job.update(updates); set_job(job_id,job); return job

def list_jobs(owner_token:str|None=None)->list[dict[str,Any]]:
 with _lock, _connect() as db:
  rows=db.execute("SELECT payload FROM jobs ORDER BY updated_at DESC").fetchall()
 all_jobs = [json.loads(r["payload"]) for r in rows]
 if owner_token is None:
  return all_jobs
 return [j for j in all_jobs if j.get("owner_token") == owner_token]

def delete_job(job_id:str)->None:
 with _lock, _connect() as db:
  db.execute("DELETE FROM jobs WHERE id=?",(job_id,))
  db.execute("DELETE FROM idempotency WHERE job_id=?",(job_id,))

def recover_jobs()->None:
 from app.i18n import get_km
 for job in list_jobs():
  if job.get("status") in {"queued","running"}:
   src = Path(job.get("source_path") or "")
   if src.is_file() and src.stat().st_size > 0:
    try:
     from app.api import POOL, worker
     update_job(job["id"], status="queued", stage="resuming")
     POOL.submit(worker, job["id"], str(src), job.get("options", {}))
     continue
    except Exception:
     pass
   update_job(job["id"],status="failed",stage="failed",error={"code":"SERVER_RESTARTED","message_en":"Job interrupted by server restart.","message_km":get_km("server_restarted")})

# ── Usage Metering & Quotas ──────────────────────────────────────────────────

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
   "INSERT INTO usage_meter(month, azure_chars) VALUES(?, ?) ON CONFLICT(month) DO UPDATE SET azure_chars = azure_chars + ?",
   (m, chars, chars)
  )
  row = db.execute("SELECT azure_chars FROM usage_meter WHERE month=?", (m,)).fetchone()
 return int(row["azure_chars"]) if row else chars

def get_daily_quota_used(token: str, day: str | None = None) -> float:
 d = day or get_current_day()
 with _lock, _connect() as db:
  row = db.execute("SELECT minutes FROM daily_quota WHERE day=? AND token=?", (d, token)).fetchone()
 return float(row["minutes"]) if row else 0.0

def add_daily_quota_used(token: str, minutes: float, day: str | None = None) -> float:
 d = day or get_current_day()
 with _lock, _connect() as db:
  db.execute(
   "INSERT INTO daily_quota(day, token, minutes) VALUES(?, ?, ?) ON CONFLICT(day, token) DO UPDATE SET minutes = minutes + ?",
   (d, token, minutes, minutes)
  )
  row = db.execute("SELECT minutes FROM daily_quota WHERE day=? AND token=?", (d, token)).fetchone()
 return float(row["minutes"]) if row else minutes

def cleanup_expired_jobs(ttl_hours: int = 24) -> int:
 cutoff = time.time() - (ttl_hours * 3600)
 count = 0
 with _lock, _connect() as db:
  rows = db.execute("SELECT id, payload FROM jobs WHERE updated_at < ?", (cutoff,)).fetchall()
  for row in rows:
   jid = row["id"]
   db.execute("DELETE FROM jobs WHERE id=?", (jid,))
   db.execute("DELETE FROM idempotency WHERE job_id=?", (jid,))
   count += 1
 return count
