from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException, Request

log = logging.getLogger("dubber.auth")

ROOT = Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "uploads"
OUTPUTS = ROOT / "outputs"
CHUNK_DIR = UPLOADS / "_chunks"

_LOCK = threading.RLock()
_TOKEN_TIMESTAMPS: dict[str, list[float]] = {}
_IP_TIMESTAMPS: dict[str, list[float]] = {}


def get_allowed_tokens() -> set[str]:
    raw = os.getenv("ACCESS_TOKENS", "soundme-test-token,soundme-dev")
    return {t.strip() for t in raw.split(",") if t.strip()}


def verify_token(token: str | None) -> bool:
    if not token or not token.strip():
        return False
    return token.strip() in get_allowed_tokens()


def extract_token_from_request(request: Request) -> str | None:
    # 1. Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth_header and auth_header.strip().lower().startswith("bearer "):
        return auth_header.strip()[7:].strip()
    # 2. X-Access-Token: <token>
    alt_header = request.headers.get("X-Access-Token") or request.headers.get("x-access-token")
    if alt_header and alt_header.strip():
        return alt_header.strip()
    # 3. Query param ?token=<token>
    token_param = request.query_params.get("token")
    if token_param and token_param.strip():
        return token_param.strip()
    return None


def check_rate_limit(token: str, client_ip: str) -> None:
    now = time.time()
    rpm = int(os.getenv("RATE_LIMIT_RPM", "120"))
    window = 60.0

    with _LOCK:
        # Check token limit
        t_list = _TOKEN_TIMESTAMPS.setdefault(token, [])
        _TOKEN_TIMESTAMPS[token] = [ts for ts in t_list if now - ts < window]
        if len(_TOKEN_TIMESTAMPS[token]) >= rpm:
            oldest = _TOKEN_TIMESTAMPS[token][0]
            retry_after = max(1, int(window - (now - oldest)))
            raise HTTPException(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                detail={
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit of {rpm} requests/min exceeded for access token. Retry in {retry_after}s.",
                },
            )
        _TOKEN_TIMESTAMPS[token].append(now)

        # Check IP limit
        if client_ip:
            ip_list = _IP_TIMESTAMPS.setdefault(client_ip, [])
            _IP_TIMESTAMPS[client_ip] = [ts for ts in ip_list if now - ts < window]
            if len(_IP_TIMESTAMPS[client_ip]) >= (rpm * 2):
                oldest = _IP_TIMESTAMPS[client_ip][0]
                retry_after = max(1, int(window - (now - oldest)))
                raise HTTPException(
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                    detail={
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": f"Rate limit of {rpm * 2} requests/min exceeded for IP. Retry in {retry_after}s.",
                    },
                )
            _IP_TIMESTAMPS[client_ip].append(now)


def get_current_token(request: Request) -> str:
    token = extract_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Access code required. Pass Authorization: Bearer <token> or ?token=<token>.",
            },
        )
    if not verify_token(token):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_TOKEN",
                "message": "Invalid or revoked access code.",
            },
        )
    client_ip = request.client.host if request.client else ""
    check_rate_limit(token, client_ip)
    return token


def check_queue_capacity() -> None:
    from app.storage import list_jobs
    from app.i18n import get_km

    max_queue = int(os.getenv("MAX_QUEUE_CAP", "5"))
    all_jobs = list_jobs()
    queued = sum(1 for j in all_jobs if j.get("status") == "queued")

    if queued >= max_queue:
        est_min = max(2, queued * 2)
        km_busy = f"ម៉ាស៊ីនមេរវល់ខ្លាំង (មាន {queued} ការងារកំពុងរង់ចាំ)។ រយៈពេលរង់ចាំប្រហែល ~{est_min} នាទី។"
        en_busy = f"Server is busy ({queued} jobs queued, limit={max_queue}). Estimated wait: ~{est_min} min."
        raise HTTPException(
            status_code=429,
            headers={"Retry-After": "60"},
            detail={
                "code": "SERVER_BUSY",
                "message_en": en_busy,
                "message_km": km_busy,
            },
        )


def check_daily_quota(token: str, add_minutes: float = 0.0) -> None:
    from app.storage import get_daily_quota_used

    quota_max = float(os.getenv("DAILY_QUOTA_MINUTES", "60.0"))
    used = get_daily_quota_used(token)
    if used + add_minutes > quota_max:
        km_err = f"កូតាប្រចាំថ្ងៃ {quota_max:.0f} នាទីត្រូវបានប្រើប្រាស់អស់ហើយ។"
        en_err = f"Daily quota of {quota_max:.0f} minutes reached for this access code."
        raise HTTPException(
            status_code=429,
            detail={
                "code": "DAILY_QUOTA_EXCEEDED",
                "message_en": en_err,
                "message_km": km_err,
            },
        )


def cleanup_expired_files(ttl_hours: int | None = None) -> dict[str, int]:
    hours = ttl_hours if ttl_hours is not None else int(os.getenv("CLEANUP_TTL_HOURS", "24"))
    cutoff = time.time() - (hours * 3600)
    cleaned_uploads = 0
    cleaned_chunks = 0
    cleaned_outputs = 0

    from app.storage import cleanup_expired_jobs

    deleted_db_jobs = cleanup_expired_jobs(hours)

    if UPLOADS.exists():
        for item in UPLOADS.iterdir():
            if item.name == "_chunks":
                for c in item.iterdir():
                    try:
                        if c.stat().st_mtime < cutoff:
                            shutil.rmtree(c, ignore_errors=True)
                            cleaned_chunks += 1
                    except Exception:
                        pass
                continue
            if item.is_dir() and item.name not in {"_preview", "_cache"}:
                try:
                    if item.stat().st_mtime < cutoff:
                        shutil.rmtree(item, ignore_errors=True)
                        cleaned_uploads += 1
                except Exception:
                    pass

    if OUTPUTS.exists():
        for item in OUTPUTS.iterdir():
            if item.is_dir():
                try:
                    if item.stat().st_mtime < cutoff:
                        shutil.rmtree(item, ignore_errors=True)
                        cleaned_outputs += 1
                except Exception:
                    pass

    return {
        "deleted_db_jobs": deleted_db_jobs,
        "cleaned_uploads": cleaned_uploads,
        "cleaned_chunks": cleaned_chunks,
        "cleaned_outputs": cleaned_outputs,
    }
