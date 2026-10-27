"""Google OAuth 2.0 OIDC integration for Soundme.

Routes
------
GET  /auth/me          — current identity (public, never 401)
GET  /auth/google      — redirect to Google consent screen
GET  /auth/callback    — handle Google redirect, set HttpOnly session cookie
POST /auth/logout      — clear session and revoke server-side session

Required env vars (optional — routes return 503 if not set):
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  GOOGLE_REDIRECT_URI    (e.g. https://soundme.example.com/auth/callback)
  SECRET_KEY             (required for SessionMiddleware — set always)
  GOOGLE_ALLOWED_EMAILS  (optional comma-separated list of permitted emails)
  GOOGLE_ALLOWED_DOMAINS (optional comma-separated list of permitted domains)

Session cookie is HttpOnly, SameSite=Lax, signed by SECRET_KEY.
Tokens are NEVER placed in URLs or JS-accessible storage.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth import clear_session, google_sub_to_user_id, set_session

log = logging.getLogger("dubber.google_auth")

router = APIRouter(prefix="/auth", tags=["auth"])

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def _google_configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def _get_redirect_uri(request: Request) -> str:
    configured = os.getenv("GOOGLE_REDIRECT_URI")
    if configured:
        return configured
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    if host:
        return f"{proto}://{host}/auth/callback"
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/callback"


def check_email_allowed(email: str) -> bool:
    """Check verified email against optional GOOGLE_ALLOWED_EMAILS or GOOGLE_ALLOWED_DOMAINS."""
    allowed_emails_env = os.getenv("GOOGLE_ALLOWED_EMAILS", "").strip()
    allowed_domains_env = os.getenv("GOOGLE_ALLOWED_DOMAINS", "").strip()
    if not allowed_emails_env and not allowed_domains_env:
        return True  # Open to all verified Google accounts
    low_email = email.lower()
    if allowed_emails_env:
        allowed = {e.strip().lower() for e in allowed_emails_env.split(",") if e.strip()}
        if low_email in allowed:
            return True
    if allowed_domains_env:
        domains = {d.strip().lower() for d in allowed_domains_env.split(",") if d.strip()}
        if "@" in low_email and low_email.split("@")[-1] in domains:
            return True
    return False


def verify_google_claims(
    claims: dict[str, Any],
    expected_client_id: str,
    expected_nonce: str | None = None,
) -> tuple[bool, str]:
    """Validate parsed or verified OIDC claims against security policy."""
    # 1. Audience
    aud = claims.get("aud")
    if aud != expected_client_id:
        return False, "INVALID_AUDIENCE"

    # 2. Issuer
    iss = claims.get("iss")
    if iss not in {"accounts.google.com", "https://accounts.google.com"}:
        return False, "INVALID_ISSUER"

    # 3. Expiration
    exp = claims.get("exp", 0)
    if time.time() > exp:
        return False, "TOKEN_EXPIRED"

    # 4. Nonce
    if expected_nonce and claims.get("nonce") != expected_nonce:
        return False, "INVALID_NONCE"

    # 5. Email verified claim (MUST be explicitly True)
    if not claims.get("email_verified"):
        return False, "UNVERIFIED_EMAIL"

    # 6. Email allowlist
    email = claims.get("email", "")
    if not check_email_allowed(email):
        return False, "EMAIL_NOT_ALLOWED"

    # 7. Subject
    if not claims.get("sub"):
        return False, "NO_SUB"

    return True, "OK"


def validate_google_id_token(
    id_token_str: str,
    client_id: str,
    expected_nonce: str | None = None,
) -> dict[str, Any]:
    """Cryptographically verify Google ID token using Google public keys and validate claims."""
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    req = google_requests.Request()
    claims = id_token.verify_oauth2_token(id_token_str, req, audience=client_id)
    ok, reason = verify_google_claims(claims, client_id, expected_nonce)
    if not ok:
        raise ValueError(reason)
    return claims


# ── Public endpoints ──────────────────────────────────────────────────────────

@router.get("/me")
def auth_me(request: Request) -> JSONResponse:
    """Return current identity. Returns authenticated=false if not logged in.
    Public endpoint — never returns 401. Does NOT expose raw tokens or secrets."""
    # Desktop-local mode: report as authenticated so the frontend skips the login gate
    try:
        from app.auth import is_desktop_local_request, _DESKTOP_USER_ID
        if is_desktop_local_request(request):
            return JSONResponse({
                "authenticated": True,
                "user_id": _DESKTOP_USER_ID,
                "auth_mode": "desktop_local",
                "google_email": None,
                "session_id": None,
            })
    except Exception:
        pass

    try:
        from app.auth import get_session_user
        session_user = get_session_user(request)
        if session_user:
            return JSONResponse({
                "authenticated": True,
                "user_id": session_user["user_id"],
                "auth_mode": session_user["auth_mode"],
                "google_email": session_user.get("google_email"),
                "session_id": session_user.get("session_id"),
            })
    except Exception:
        pass
    return JSONResponse({
        "authenticated": False,
        "user_id": None,
        "auth_mode": None,
        "google_email": None,
    })


@router.get("/google")
def google_login(request: Request) -> RedirectResponse:
    """Redirect to Google consent screen with anti-CSRF state and anti-replay nonce.
    Returns 503 if not configured.
    """
    if not _google_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "GOOGLE_AUTH_NOT_CONFIGURED",
                "message": (
                    "Google OAuth is not configured on this server. "
                    "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to enable it."
                ),
            },
        )
    redirect_uri = _get_redirect_uri(request)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    raw_next = str(request.query_params.get("next", "/"))
    next_url = raw_next if (raw_next.startswith("/") and not raw_next.startswith("//")) else "/"

    try:
        request.session["oauth_state"] = state
        request.session["oauth_nonce"] = nonce
        request.session["oauth_next"] = next_url
    except Exception:
        pass

    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = _GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle Google OAuth callback. Sets HttpOnly session cookie and redirects to app."""
    if error:
        log.warning("Google OAuth error from provider: %s", error)
        return RedirectResponse(url="/?auth_error=google_denied", status_code=302)

    if not _google_configured():
        raise HTTPException(status_code=503, detail={"code": "GOOGLE_AUTH_NOT_CONFIGURED"})

    # Validate state and prevent replay: pop immediately!
    expected_state = None
    expected_nonce = None
    next_url = "/"
    try:
        expected_state = request.session.pop("oauth_state", None)
        expected_nonce = request.session.pop("oauth_nonce", None)
        next_url = request.session.pop("oauth_next", "/") or "/"
    except Exception:
        pass

    if not expected_state or expected_state != state:
        log.warning("Google OAuth state mismatch: expected=%s got=%s", expected_state, state)
        return RedirectResponse(url="/?auth_error=invalid_state", status_code=302)

    if not code:
        return RedirectResponse(url="/?auth_error=missing_code", status_code=302)

    redirect_uri = _get_redirect_uri(request)
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")

    # Exchange code for tokens
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            token_data: dict[str, Any] = token_resp.json()
    except Exception as exc:
        log.error("Google token exchange failed: %s", exc)
        return RedirectResponse(url="/?auth_error=token_exchange_failed", status_code=302)

    # Validate ID token (cryptographic verification)
    id_token_raw = token_data.get("id_token")
    user_claims = None

    if id_token_raw:
        try:
            user_claims = validate_google_id_token(id_token_raw, client_id, expected_nonce)
        except Exception as exc:
            err_code = str(exc)
            log.warning("Google ID token validation failed: %s", err_code)
            if err_code in {"UNVERIFIED_EMAIL", "EMAIL_NOT_ALLOWED", "INVALID_NONCE", "TOKEN_EXPIRED", "INVALID_ISSUER", "INVALID_AUDIENCE"}:
                return RedirectResponse(url=f"/?auth_error={err_code.lower()}", status_code=302)
            return RedirectResponse(url="/?auth_error=id_token_invalid", status_code=302)

    # Fallback / additional userinfo endpoint
    if not user_claims:
        try:
            access_token = token_data.get("access_token", "")
            async with httpx.AsyncClient(timeout=10.0) as client:
                userinfo_resp = await client.get(
                    _GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                userinfo_resp.raise_for_status()
                userinfo: dict[str, Any] = userinfo_resp.json()
            if not userinfo.get("email_verified"):
                return RedirectResponse(url="/?auth_error=unverified_email", status_code=302)
            if not check_email_allowed(userinfo.get("email", "")):
                return RedirectResponse(url="/?auth_error=email_not_allowed", status_code=302)
            user_claims = userinfo
        except Exception as exc:
            log.error("Google userinfo fetch failed: %s", exc)
            return RedirectResponse(url="/?auth_error=userinfo_failed", status_code=302)

    sub = user_claims.get("sub")
    email = user_claims.get("email", "")
    if not sub:
        return RedirectResponse(url="/?auth_error=no_sub", status_code=302)

    user_id = google_sub_to_user_id(sub)
    set_session(request, user_id, "google", email, session_data={"name": user_claims.get("name")})

    log.info("Google login successful: user_id=%s", user_id)
    dest = next_url if (next_url and next_url.startswith("/") and not next_url.startswith("//")) else "/"
    sep = "&" if "?" in dest else "?"
    return RedirectResponse(url=f"{dest}{sep}login=success", status_code=302)


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    """Clear session cookie and revoke server-side session."""
    clear_session(request)
    return JSONResponse({"logged_out": True})
