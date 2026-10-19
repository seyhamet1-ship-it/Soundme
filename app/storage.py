from __future__ import annotations
import json, os, sqlite3, threading
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
        with db:
            yield db
    finally:
        db.close()

def set_job(job_id:str,payload:dict[str,Any])->None:
 with _lock, _connect() as db:
  db.execute("INSERT OR REPLACE INTO jobs(id,payload,updated_at) VALUES(?,?,unixepoch())",(job_id,json.dumps(payload,ensure_ascii=False)))
def get_job(job_id:str)->dict[str,Any]|None:
 with _lock, _connect() as db:
  row=db.execute("SELECT payload FROM jobs WHERE id=?",(job_id,)).fetchone()
 return json.loads(row["payload"]) if row else None
def get_idempotent_job(key:str)->dict[str,Any]|None:
 with _lock, _connect() as db:
  row=db.execute("SELECT job_id FROM idempotency WHERE key=?",(key,)).fetchone()
 if not row: return None
 return get_job(row["job_id"])
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
def list_jobs()->list[dict[str,Any]]:
 with _lock, _connect() as db:
  rows=db.execute("SELECT payload FROM jobs ORDER BY updated_at DESC").fetchall()
 return [json.loads(r["payload"]) for r in rows]
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
