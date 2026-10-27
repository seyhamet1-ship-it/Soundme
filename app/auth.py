"""Phase 10 auth: static-token + Google OAuth sessions, unified user_id, preserved Phase 9 controls.

Two authentication modes coexist:
  1. Token mode  — Authorization: Bearer <token> header (Phase 9 compat)
  2. Google mode — HttpOnly session cookie from /auth/google flow

Both modes produce a stable opaque user_id used for all ownership checks,
rate limiting, and daily quotas.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

log = logging.getLogger("dubber.auth")

from app.config import UPLOAD_DIR, OUTPUT_DIR

# Use config-based paths read from .env at startup.
# Falls back to hardcoded project-relative paths if UPLOAD_DIR/OUTPUT_DIR
# are not set in the environment (backward compatibility).
UPLOADS = UPLOAD_DIR
OUTPUTS = OUTPUT_DIR
CHUNK_DIR = UPLOAD_DIR / "_chunks"

_LOCK = threading.RLock()
# Rate-limit buckets keyed by user_id
_USER_TIMESTAMPS: dict[str, list[float]] = {}
_IP_TIMESTAMPS: dict[str, list[float]] = {}
# Backward-compat alias used by tests that import _TOKEN_TIMESTAMPS
_TOKEN_TIMESTAMPS = _USER_TIMESTAMPS


# ── Identity helpers ──────────────────────────────────────────────────────────

def get_allowed_tokens() -> set[str]:
    raw = os.getenv("ACCESS_TOKENS", "soundme-test-token,soundme-dev")
    return {t.strip() for t in raw.split(",") if t.strip()}


def token_to_user_id(token: str) -> str:
    """Derive a stable, opaque user_id from a static access token."""
    h = hashlib.sha256(token.encode()).hexdigest()[:16]
    return f"tok:{h}"


def google_sub_to_user_id(sub: str) -> str:
    """Derive a stable user_id from a Google subject claim."""
    return f"goo:{sub}"


def verify_token(token: str | None) -> bool:
    if not token or not token.strip():
        return False
    return token.strip() in get_allowed_tokens()


# ── Session helpers ───────────────────────────────────────────────────────────

def get_session_ttl() -> int:
    return int(os.getenv("SESSION_TTL_HOURS", "24")) * 3600


def get_session_user(request: Request) -> dict[str, Any] | None:
    """Read and validate the session cookie against server-side session store.
    
    Dual-layer security:
    1. Signed cookie must be valid and unexpired
    2. Session ID must exist in server-side DB (permits instant revocation)
    """
    try:
        session = request.session
        user_id = session.get("user_id")
        session_id = session.get("session_id")
        expires_at = session.get("expires_at", 0)
        now = time.time()
        if not user_id or now >= expires_at:
            return None

        # Verify against server-side sessions table if session_id is present
        if session_id:
            from app.storage import get_session
            db_session = get_session(session_id)
            if not db_session or db_session.get("user_id") != user_id:
                # Revoked or expired server-side
                clear_session(request)
                return None

        return {
            "session_id": session_id,
            "user_id": user_id,
            "auth_mode": session.get("auth_mode", "token"),
            "google_email": session.get("google_email"),
        }
    except Exception:
        pass
    return None


def set_session(
    request: Request,
    user_id: str,
    auth_mode: str,
    google_email: str | None = None,
    session_data: dict[str, Any] | None = None,
) -> str:
    """Write a session into the server-side DB and signed cookie store.
    Returns the cryptographically secure session_id.
    """
    import secrets
    from app.storage import create_session

    session_id = secrets.token_hex(32)
    expires_at = time.time() + get_session_ttl()
    extra = dict(session_data or {})
    if google_email:
        extra["google_email"] = google_email

    # Persist in DB
    try:
        create_session(session_id, user_id, auth_mode, expires_at, extra)
    except Exception as exc:
        log.warning("Failed to store session in DB: %s", exc)

    # Store in signed cookie
    try:
        request.session["session_id"] = session_id
        request.session["user_id"] = user_id
        request.session["auth_mode"] = auth_mode
        request.session["expires_at"] = expires_at
        if google_email:
            request.session["google_email"] = google_email
    except Exception:
        pass  # Session middleware may not be present in unit tests
    return session_id


def clear_session(request: Request) -> None:
    """Revoke server-side session and clear cookie."""
    try:
        session_id = request.session.get("session_id")
        if session_id:
            from app.storage import revoke_session
            revoke_session(session_id)
    except Exception:
        pass
    try:
        request.session.clear()
    except Exception:
        pass


# ── Token extraction ──────────────────────────────────────────────────────────

def extract_token_from_request(request: Request) -> str | None:
    """Extract static access token from Authorization header or X-Access-Token.

    NOTE: ?token= query param is intentionally NOT used here — tokens must
    not appear in URLs (server logs, Referer headers, browser history).
    The frontend uses Authorization header or session cookie instead.
    """
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth_header and auth_header.strip().lower().startswith("bearer "):
        return auth_header.strip()[7:].strip()
    alt_header = request.headers.get("X-Access-Token") or request.headers.get("x-access-token")
    if alt_header and alt_header.strip():
        return alt_header.strip()
    return None


# ── Rate limiting ─────────────────────────────────────────────────────────────

def check_rate_limit(user_id: str, client_ip: str) -> None:
    now = time.time()
    rpm = int(os.getenv("RATE_LIMIT_RPM", "120"))
    window = 60.0

    with _LOCK:
        # Per-user_id limit
        u_list = _USER_TIMESTAMPS.setdefault(user_id, [])
        _USER_TIMESTAMPS[user_id] = [ts for ts in u_list if now - ts < window]
        if len(_USER_TIMESTAMPS[user_id]) >= rpm:
            oldest = _USER_TIMESTAMPS[user_id][0]
            retry_after = max(1, int(window - (now - oldest)))
            raise HTTPException(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                detail={
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit of {rpm} requests/min exceeded. Retry in {retry_after}s.",
                },
            )
        _USER_TIMESTAMPS[user_id].append(now)

        # Per-IP limit
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


# ── Desktop-local bypass ──────────────────────────────────────────────────────

_DESKTOP_USER_ID = "desktop:local"
_LOOPBACK_IPS = {"127.0.0.1", "::1", "localhost", "testclient"}


def is_desktop_local_request(request: Request) -> bool:
    """Return True only when:
      - DESKTOP_LOCAL_MODE=1 is set in the process environment (set by Electron backend-manager),
      - AND the request originates from a loopback address (127.0.0.1 / ::1 / localhost / testclient).

    This ensures the bypass is never active on a public-facing server.
    """
    if os.getenv("DESKTOP_LOCAL_MODE", "0").strip().lower() not in {"1", "true", "yes"}:
        return False
    client_host = (request.client.host if request.client else "") or ""
    return client_host in _LOOPBACK_IPS


# ── Unified authentication ────────────────────────────────────────────────────

def get_current_user(request: Request) -> dict[str, Any]:
    """Unified auth: Bearer token header OR session cookie (Google or browser session).

    When DESKTOP_LOCAL_MODE=1 and the request is from loopback, authentication
    is automatically satisfied with a stable synthetic desktop-local identity.
    This bypass is ONLY active for requests arriving via 127.0.0.1 from the
    local Electron shell; it is never triggered on remote connections.

    Header takes precedence: if an explicit Authorization header is passed,
    authenticate with it directly.
    Otherwise fall back to session cookie.
    Raises 401 if unauthenticated.
    """
    # 0. Desktop-local mode: loopback-only auto-auth (Electron desktop app)
    if is_desktop_local_request(request):
        return {
            "user_id": _DESKTOP_USER_ID,
            "auth_mode": "desktop_local",
            "google_email": None,
        }

    # 1. Try Bearer token header first (explicit header takes precedence)
    token = extract_token_from_request(request)
    if token:
        if not verify_token(token):
            raise HTTPException(
                status_code=401,
                detail={"code": "INVALID_TOKEN", "message": "Invalid or revoked access code."},
            )
        user_id = token_to_user_id(token)
        client_ip = request.client.host if request.client else ""
        check_rate_limit(user_id, client_ip)
        return {"user_id": user_id, "auth_mode": "token", "google_email": None, "_token": token}

    # 2. Try session cookie (Google OAuth or browser session)
    session_user = get_session_user(request)
    if session_user:
        client_ip = request.client.host if request.client else ""
        check_rate_limit(session_user["user_id"], client_ip)
        return session_user

    # 3. Unauthenticated
    raise HTTPException(
        status_code=401,
        detail={
            "code": "UNAUTHORIZED",
            "message": (
                "Authentication required. "
                "Use Authorization: Bearer <token> or log in via /auth/google."
            ),
        },
    )


def get_current_token(request: Request) -> str:
    """Backward-compat shim for Phase 9 callers that expect a raw token string.

    For token-mode users: returns the actual access token.
    For Google-session users: returns the user_id (used as owner key in DB).
    All new code should use get_current_user() instead.
    """
    user = get_current_user(request)
    raw_token = user.get("_token")
    if raw_token:
        return raw_token
    # Google / cookie-only session: return user_id as the ownership key
    return user["user_id"]


def get_user_id_from_request(request: Request) -> str:
    """Get the stable user_id for the current authenticated request."""
    return get_current_user(request)["user_id"]


# ── Abuse controls ────────────────────────────────────────────────────────────

def check_queue_capacity() -> None:
    from app.storage import list_jobs
    from app.i18n import get_km

    max_queue = int(os.getenv("MAX_QUEUE_CAP", "5"))
    all_jobs = list_jobs()
    queued = sum(1 for j in all_jobs if j.get("status") == "queued")

    if queued >= max_queue:
        est_min = max(2, queued * 2)
        km_busy = (
            f"ម៉ាស៊ីនមេរវល់ខ្លាំង (មាន {queued} ការងារកំពុងរង់ចាំ)។ "
            f"រយៈពេលរង់ចាំប្រហែល ~{est_min} នាទី។"
        )
        en_busy = (
            f"Server is busy ({queued} jobs queued, limit={max_queue}). "
            f"Estimated wait: ~{est_min} min."
        )
        raise HTTPException(
            status_code=429,
            headers={"Retry-After": "60"},
            detail={"code": "SERVER_BUSY", "message_en": en_busy, "message_km": km_busy},
        )


def check_daily_quota(user_id: str, add_minutes: float = 0.0) -> None:
    """Check daily quota by user_id. Raises 429 if quota exceeded."""
    from app.storage import get_daily_quota_used

    quota_max = float(os.getenv("DAILY_QUOTA_MINUTES", "60.0"))
    used = get_daily_quota_used(user_id)
    if used + add_minutes > quota_max:
        km_err = f"កូតាប្រចាំថ្ងៃ {quota_max:.0f} នាទីត្រូវបានប្រើប្រាស់អស់ហើយ។"
        en_err = f"Daily quota of {quota_max:.0f} minutes reached for this account."
        raise HTTPException(
            status_code=429,
            detail={"code": "DAILY_QUOTA_EXCEEDED", "message_en": en_err, "message_km": km_err},
        )


# ── TTL cleanup ───────────────────────────────────────────────────────────────

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
