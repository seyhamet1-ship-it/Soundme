from __future__ import annotations

import asyncio
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from starlette.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import router, public_router
from app.auth import cleanup_expired_files
from app.google_auth import router as google_auth_router
from app.providers import is_models_ready, tts_provider_health, warm_models
from app.storage import recover_jobs, run_migration

log = logging.getLogger("dubber")


def check_storage_drive() -> bool:
    data_dir = str(os.getenv("UPLOAD_DIR") or Path(__file__).resolve().parents[1] / "uploads")
    low = data_dir.lower()
    is_virtual = any(kw in low for kw in ["google drive", "my drive", "onedrive", "dropbox", "cloudstorage", "smb", "nfs", "\\\\"])
    if is_virtual:
        msg = (
            f"WARNING: [STORAGE_CONTENTION] DATA_DIR is on a network/virtual drive ({data_dir}). "
            "Cloud synchronization and antivirus contention introduce severe I/O latency "
            "(probe ~13s, cold model load ~160s). For production deployment, configure DATA_DIR on a local SSD."
        )
        print(f"\n{'!' * 70}\n{msg}\n{'!' * 70}\n", flush=True)
        log.warning(msg)
    return is_virtual


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Check data directory for network/virtual filesystem warnings
    check_storage_drive()
    # Phase 10: backfill user_id on existing jobs from owner_token (safe, idempotent)
    run_migration()
    # Recover any running jobs interrupted by server restart
    recover_jobs()
    # TTL Cleanup for stale uploads and outputs (default 24h)
    try:
        cleanup_expired_files()
    except Exception as exc:
        log.warning("Initial TTL cleanup failed: %s", exc)

    # Warm models at startup so /api/health reports models_ready
    if os.getenv("WARM_MODELS_AT_STARTUP", "1").strip().lower() not in {"0", "false", "no"}:
        asyncio.create_task(asyncio.to_thread(warm_models))

    yield


app = FastAPI(
    title="Khmer Video Dubber",
    version="1.0.0",
    description="Translate Chinese or English video into Khmer speech and subtitles.",
    lifespan=lifespan,
)

# Session middleware (must be added before CORS and route handlers)
# SECRET_KEY must be set in production; falls back to a random key (sessions won't persist across restarts)
_secret_key = os.getenv("SECRET_KEY") or os.urandom(32).hex()
app.add_middleware(
    SessionMiddleware,
    secret_key=_secret_key,
    session_cookie="soundme_session",
    max_age=int(os.getenv("SESSION_TTL_HOURS", "24")) * 3600,
    https_only=os.getenv("SESSION_HTTPS_ONLY", "0").strip().lower() not in {"0", "false", "no"},
    same_site="lax",
)

# CORS limited to public origin
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "media-src 'self' blob: data:; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none';"
    )
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Log full traceback internally, but never leak stack traces to client
    log.exception("Unhandled server exception at %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An internal server error occurred. Please contact the administrator.",
        },
    )


app.include_router(google_auth_router)
app.include_router(public_router)
app.include_router(router)


@app.get("/api/health")
def health():
    tools = {name: shutil.which(name) is not None for name in ("ffmpeg", "ffprobe")}
    storage_warn = check_storage_drive()
    ready = is_models_ready()
    tts_info = tts_provider_health()

    status_str = "ok" if (all(tools.values()) and ready) else ("warming" if not ready else "degraded")

    from app.config import MAX_UPLOAD_MB, MAX_DURATION_MIN
    return {
        "status": status_str,
        "models_ready": ready,
        "tools": tools,
        "storage_virtual_warning": storage_warn,
        "asr": "faster-whisper small",
        "translation": os.getenv("TRANSLATION_PROVIDER", "nllb-ct2-int8"),
        "tts": tts_info,
        "workers": int(os.getenv("MAX_CONCURRENT_JOBS", "1")),
        "max_upload_mb": MAX_UPLOAD_MB,
        "max_duration_min": MAX_DURATION_MIN,
    }


ROOT = Path(__file__).resolve().parents[1]
_DIST = ROOT / "frontend" / "dist"
if (_DIST / "index.html").is_file():
    if (_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("auth/"):
            raise HTTPException(status_code=404, detail="Not Found")
        file_path = _DIST / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_DIST / "index.html")
