"""Google OAuth 2.0 OIDC integration for Soundme.

Routes
------
GET  /auth/me          — current identity (public, never 401)
GET  /auth/google      — redirect to Google consent screen
GET  /auth/callback    — handle Google redirect, set HttpOnly session cookie
POST /auth/logout      — clear session

Required env vars (all optional — routes return 503 if not set):
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  GOOGLE_REDIRECT_URI    (e.g. https://soundme.example.com/auth/callback)
  SECRET_KEY             (required for SessionMiddleware — set always)

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
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/callback"


# ── Public endpoints ──────────────────────────────────────────────────────────

@router.get("/me")
def auth_me(request: Request) -> JSONResponse:
    """Return current identity. Returns authenticated=false if not logged in.
    Public endpoint — never returns 401. Does NOT expose raw tokens or secrets."""
    try:
        session = request.session
        user_id = session.get("user_id")
        expires_at = session.get("expires_at", 0)
        if user_id and time.time() < expires_at:
            return JSONResponse({
                "authenticated": True,
                "user_id": user_id,
                "auth_mode": session.get("auth_mode", "token"),
                "google_email": (
                    session.get("google_email")
                    if session.get("auth_mode") == "google"
                    else None
                ),
                "expires_at": int(expires_at),
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
    """Redirect to Google consent screen. Returns 503 if not configured."""
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
    next_url = str(request.query_params.get("next", "/"))

    try:
        request.session["oauth_state"] = state
        request.session["oauth_next"] = next_url
    except Exception:
        pass

    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
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
    """Handle Google OAuth callback. Sets session cookie and redirects to app."""
    if error:
        log.warning("Google OAuth error: %s", error)
        return RedirectResponse(url="/?auth_error=google_denied", status_code=302)

    if not _google_configured():
        raise HTTPException(status_code=503, detail={"code": "GOOGLE_AUTH_NOT_CONFIGURED"})

    # Verify CSRF state
    try:
        expected_state = request.session.get("oauth_state")
        next_url = request.session.get("oauth_next", "/")
        if not expected_state or expected_state != state:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_OAUTH_STATE", "message": "OAuth state mismatch."},
            )
    except HTTPException:
        raise
    except Exception:
        next_url = "/"

    if not code:
        raise HTTPException(
            status_code=400,
            detail={"code": "MISSING_CODE", "message": "No authorization code received."},
        )

    redirect_uri = _get_redirect_uri(request)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                    "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            token_data: dict[str, Any] = token_resp.json()
    except Exception as exc:
        log.error("Google token exchange failed: %s", exc)
        return RedirectResponse(url="/?auth_error=token_exchange_failed", status_code=302)

    try:
        access_token = token_data.get("access_token", "")
        async with httpx.AsyncClient(timeout=10.0) as client:
            userinfo_resp = await client.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_resp.raise_for_status()
            userinfo: dict[str, Any] = userinfo_resp.json()
    except Exception as exc:
        log.error("Google userinfo fetch failed: %s", exc)
        return RedirectResponse(url="/?auth_error=userinfo_failed", status_code=302)

    sub = userinfo.get("sub")
    email = userinfo.get("email", "")
    if not sub:
        return RedirectResponse(url="/?auth_error=no_sub", status_code=302)

    user_id = google_sub_to_user_id(sub)
    set_session(request, user_id, "google", email)

    try:
        request.session.pop("oauth_state", None)
        request.session.pop("oauth_next", None)
    except Exception:
        pass

    log.info("Google login: user_id=%s email=%s", user_id, email)
    return RedirectResponse(url=next_url or "/", status_code=302)


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    """Clear session cookie and log out."""
    clear_session(request)
    return JSONResponse({"logged_out": True})
