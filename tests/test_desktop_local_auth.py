"""
Tests for desktop-local authentication bypass.

Verifies that:
  1. is_desktop_local_request() returns True only when DESKTOP_LOCAL_MODE=1 AND
     the request originates from a loopback address.
  2. get_current_user() returns the synthetic desktop:local user for qualifying requests.
  3. Non-loopback requests are still rejected (401) even when DESKTOP_LOCAL_MODE=1.
  4. Loopback requests without DESKTOP_LOCAL_MODE=1 are still rejected (401).
"""

import os
import importlib
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(client_host: str, headers: dict | None = None):
    """Return a minimal mock Request with the given client host."""
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = client_host
    req.headers = headers or {}
    return req


@pytest.fixture(autouse=True)
def clean_desktop_mode():
    yield
    os.environ.pop("DESKTOP_LOCAL_MODE", None)
    import app.auth as auth_mod
    importlib.reload(auth_mod)


def _reload_auth(desktop_mode: str):
    """Set DESKTOP_LOCAL_MODE in os.environ and reload app.auth.

    NOTE: The env var must remain set *after* this function returns because
    is_desktop_local_request() calls os.getenv() at invocation time, not at
    import time.  Callers are responsible for restoring os.environ (the test
    class setUp/tearDown or per-test patch.dict usage handles cleanup).
    """
    os.environ["DESKTOP_LOCAL_MODE"] = desktop_mode
    import app.auth as auth_mod
    importlib.reload(auth_mod)
    return auth_mod


# ---------------------------------------------------------------------------
# is_desktop_local_request
# ---------------------------------------------------------------------------

class TestIsDesktopLocalRequest:
    """Unit tests for the is_desktop_local_request() helper."""

    def test_loopback_ipv4_with_mode_enabled(self):
        with patch.dict(os.environ, {"DESKTOP_LOCAL_MODE": "1"}):
            auth = _reload_auth("1")
            req = _make_request("127.0.0.1")
            assert auth.is_desktop_local_request(req) is True

    def test_loopback_ipv6_with_mode_enabled(self):
        with patch.dict(os.environ, {"DESKTOP_LOCAL_MODE": "1"}):
            auth = _reload_auth("1")
            req = _make_request("::1")
            assert auth.is_desktop_local_request(req) is True

    def test_loopback_localhost_with_mode_enabled(self):
        with patch.dict(os.environ, {"DESKTOP_LOCAL_MODE": "1"}):
            auth = _reload_auth("1")
            req = _make_request("localhost")
            assert auth.is_desktop_local_request(req) is True

    def test_loopback_with_mode_disabled(self):
        with patch.dict(os.environ, {"DESKTOP_LOCAL_MODE": "0"}):
            auth = _reload_auth("0")
            req = _make_request("127.0.0.1")
            assert auth.is_desktop_local_request(req) is False

    def test_loopback_with_mode_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "DESKTOP_LOCAL_MODE"}
        with patch.dict(os.environ, env, clear=True):
            import app.auth as auth_mod
            importlib.reload(auth_mod)
            req = _make_request("127.0.0.1")
            assert auth_mod.is_desktop_local_request(req) is False

    def test_non_loopback_with_mode_enabled(self):
        with patch.dict(os.environ, {"DESKTOP_LOCAL_MODE": "1"}):
            auth = _reload_auth("1")
            req = _make_request("10.0.0.5")
            assert auth.is_desktop_local_request(req) is False

    def test_non_loopback_with_mode_disabled(self):
        with patch.dict(os.environ, {"DESKTOP_LOCAL_MODE": "0"}):
            auth = _reload_auth("0")
            req = _make_request("203.0.113.42")
            assert auth.is_desktop_local_request(req) is False

    def test_empty_client_host(self):
        """Missing client host should not crash and should return False."""
        with patch.dict(os.environ, {"DESKTOP_LOCAL_MODE": "1"}):
            auth = _reload_auth("1")
            req = MagicMock()
            req.client = None
            assert auth.is_desktop_local_request(req) is False


# ---------------------------------------------------------------------------
# get_current_user — desktop-local bypass
# ---------------------------------------------------------------------------

class TestGetCurrentUserDesktopLocal:
    """Integration-level tests for get_current_user() desktop-local bypass."""

    def test_loopback_desktop_mode_returns_synthetic_user(self):
        auth = _reload_auth("1")
        req = _make_request("127.0.0.1")
        result = auth.get_current_user(request=req)
        assert result["user_id"] == "desktop:local"
        assert result["auth_mode"] == "desktop_local"

    def test_non_loopback_desktop_mode_raises_401(self):
        auth = _reload_auth("1")
        req = _make_request("192.168.1.100")
        with pytest.raises(HTTPException) as exc_info:
            auth.get_current_user(request=req)
        assert exc_info.value.status_code == 401

    def test_loopback_no_desktop_mode_raises_401(self):
        auth = _reload_auth("0")
        req = _make_request("127.0.0.1")
        with pytest.raises(HTTPException) as exc_info:
            auth.get_current_user(request=req)
        assert exc_info.value.status_code == 401

    def test_desktop_local_user_has_expected_fields(self):
        auth = _reload_auth("1")
        req = _make_request("127.0.0.1")
        result = auth.get_current_user(request=req)
        # Must not expose real credentials
        assert "google_email" in result
        assert result.get("google_email") is None
        assert result["user_id"] == "desktop:local"

    def test_ipv6_loopback_desktop_mode(self):
        auth = _reload_auth("1")
        req = _make_request("::1")
        result = auth.get_current_user(request=req)
        assert result["user_id"] == "desktop:local"


# ---------------------------------------------------------------------------
# /auth/me endpoint — desktop-local response
# ---------------------------------------------------------------------------

class TestAuthMeDesktopLocal:
    """Smoke test that /auth/me logic returns authenticated=True for desktop loopback."""

    def test_auth_me_loopback_desktop_mode(self):
        """auth_me() called with a loopback request + DESKTOP_LOCAL_MODE=1 → authenticated."""
        with patch.dict(os.environ, {"DESKTOP_LOCAL_MODE": "1"}):
            import app.auth as auth_mod
            importlib.reload(auth_mod)
            from app.google_auth import auth_me  # noqa: PLC0415

            req = _make_request("127.0.0.1")
            resp = auth_me(req)
            data = resp.body
            import json
            data = json.loads(data)

        assert data.get("authenticated") is True
        assert data.get("user_id") == "desktop:local"
        assert data.get("auth_mode") == "desktop_local"

    def test_auth_me_non_loopback_returns_unauthenticated(self):
        """auth_me() from a non-loopback host returns authenticated=False (no session)."""
        with patch.dict(os.environ, {"DESKTOP_LOCAL_MODE": "0"}):
            import app.auth as auth_mod
            importlib.reload(auth_mod)
            from app.google_auth import auth_me  # noqa: PLC0415

            req = _make_request("10.0.0.1")
            resp = auth_me(req)
            import json
            data = json.loads(resp.body)

        assert data.get("authenticated") is False
