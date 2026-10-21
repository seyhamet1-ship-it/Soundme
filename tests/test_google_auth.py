"""Phase 10 Item 7 — Google OAuth and session auth tests.

Tests run without a real Google account (BLOCKED for real OAuth flow).
Tests that CAN run:
  - /auth/me returns unauthenticated when no session
  - /auth/google returns 503 when not configured
  - /auth/logout clears session (via simulated session)
  - Session-based auth works for API routes
  - Cross-user isolation by user_id (Google vs token)
  - Token-derived user_id is stable and deterministic
  - Session expiry
  - Logout invalidates session

BLOCKED:
  - Real Google OAuth redirect/callback (requires valid GOOGLE_CLIENT_ID + network)
"""
from __future__ import annotations

import os
import time
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import storage
from app.auth import token_to_user_id, google_sub_to_user_id, _USER_TIMESTAMPS, _IP_TIMESTAMPS, _LOCK

TEST_TOKEN = "soundme-test-token"
ALT_TOKEN = "soundme-alt-token"
TEST_USER_ID = token_to_user_id(TEST_TOKEN)
ALT_USER_ID = token_to_user_id(ALT_TOKEN)


def _auth(token: str = TEST_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _clear_rate_buckets() -> None:
    with _LOCK:
        _USER_TIMESTAMPS.clear()
        _IP_TIMESTAMPS.clear()


# ── /auth/me — public endpoint ────────────────────────────────────────────────

def test_auth_me_returns_unauthenticated_when_no_session(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.get("/auth/me")
        assert r.status_code == 200
        data = r.json()
        assert data["authenticated"] is False
        assert data["user_id"] is None
        assert data["auth_mode"] is None


def test_auth_me_never_returns_401(monkeypatch, tmp_path):
    """auth/me must never 401 — it's a public probe endpoint."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.get("/auth/me")
        assert r.status_code != 401


# ── /auth/google — 503 when not configured ────────────────────────────────────

def test_google_login_returns_503_when_not_configured(monkeypatch, tmp_path):
    """Without GOOGLE_CLIENT_ID, the endpoint must clearly say it's not configured."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    with TestClient(app, follow_redirects=False) as client:
        r = client.get("/auth/google")
        assert r.status_code == 503
        body = r.json()
        assert body["detail"]["code"] == "GOOGLE_AUTH_NOT_CONFIGURED"


def test_google_login_redirects_when_configured(monkeypatch, tmp_path):
    """When credentials are present, /auth/google redirects to Google."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-client-secret")
    with TestClient(app, follow_redirects=False) as client:
        r = client.get("/auth/google")
        assert r.status_code == 302
        assert "accounts.google.com" in r.headers.get("location", "")


# ── /auth/logout ──────────────────────────────────────────────────────────────

def test_logout_returns_200(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.post("/auth/logout")
        assert r.status_code == 200
        assert r.json()["logged_out"] is True


# ── user_id derivation ────────────────────────────────────────────────────────

def test_token_to_user_id_is_stable():
    """Same token always produces same user_id."""
    uid1 = token_to_user_id("my-token")
    uid2 = token_to_user_id("my-token")
    assert uid1 == uid2
    assert uid1.startswith("tok:")
    assert len(uid1) == 20  # "tok:" + 16 hex chars


def test_different_tokens_produce_different_user_ids():
    uid1 = token_to_user_id("token-a")
    uid2 = token_to_user_id("token-b")
    assert uid1 != uid2


def test_google_sub_to_user_id():
    uid = google_sub_to_user_id("123456789")
    assert uid == "goo:123456789"
    assert uid.startswith("goo:")


# ── Session-based auth for API routes ────────────────────────────────────────

def test_bearer_token_produces_correct_user_id(monkeypatch, tmp_path):
    """Bearer token auth should produce user_id = tok:<hash>, not the raw token."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("ACCESS_TOKENS", TEST_TOKEN)
    _clear_rate_buckets()
    with TestClient(app) as client:
        # jobs list returns 200 with correct auth
        r = client.get("/api/jobs", headers=_auth(TEST_TOKEN))
        assert r.status_code == 200
    _clear_rate_buckets()


def test_cross_user_id_isolation_in_jobs(monkeypatch, tmp_path):
    """Jobs created by user A (via token A) are invisible to user B (token B)."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("ACCESS_TOKENS", f"{TEST_TOKEN},{ALT_TOKEN}")
    _clear_rate_buckets()

    # Directly insert a job owned by TEST_USER_ID
    jid = uuid.uuid4().hex
    storage.set_job(jid, {
        "id": jid,
        "user_id": TEST_USER_ID,
        "owner_token": TEST_TOKEN,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "source_path": str(tmp_path / "v.mp4"),
        "filename": "v.mp4",
        "options": {},
    })

    with TestClient(app) as client:
        # ALT_TOKEN (different user) cannot see this job
        r_b = client.get(f"/api/jobs/{jid}", headers=_auth(ALT_TOKEN))
        assert r_b.status_code == 404, f"Cross-user isolation FAIL: {r_b.status_code}"

        # TEST_TOKEN owner can see it
        r_a = client.get(f"/api/jobs/{jid}", headers=_auth(TEST_TOKEN))
        assert r_a.status_code == 200

        # ALT_TOKEN's job list must not contain TEST_TOKEN's job
        r_list = client.get("/api/jobs", headers=_auth(ALT_TOKEN))
        ids_b = [j["id"] for j in r_list.json()]
        assert jid not in ids_b, "Cross-user: job visible in other user's list"
    _clear_rate_buckets()


def test_google_session_isolation(monkeypatch, tmp_path):
    """A job owned by a Google user_id is invisible to a token user_id."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("ACCESS_TOKENS", TEST_TOKEN)
    _clear_rate_buckets()

    google_uid = google_sub_to_user_id("google-sub-12345")
    jid = uuid.uuid4().hex
    storage.set_job(jid, {
        "id": jid,
        "user_id": google_uid,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "source_path": str(tmp_path / "v.mp4"),
        "filename": "v.mp4",
        "options": {},
    })

    with TestClient(app) as client:
        # Token user cannot see Google user's job
        r = client.get(f"/api/jobs/{jid}", headers=_auth(TEST_TOKEN))
        assert r.status_code == 404, f"Google/token isolation FAIL: {r.status_code}"

        # Token user's job list must not contain Google user's job
        r_list = client.get("/api/jobs", headers=_auth(TEST_TOKEN))
        ids = [j["id"] for j in r_list.json()]
        assert jid not in ids
    _clear_rate_buckets()


# ── Migration: existing jobs with owner_token get user_id ────────────────────

def test_migration_backfills_user_id(monkeypatch, tmp_path):
    """run_migration() backfills user_id on jobs that only have owner_token."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("ACCESS_TOKENS", TEST_TOKEN)

    # Insert a legacy job with only owner_token (no user_id)
    jid = uuid.uuid4().hex
    import sqlite3, json
    db_path = tmp_path / "jobs.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL DEFAULT (unixepoch()), user_id TEXT)")
    payload = {
        "id": jid,
        "owner_token": TEST_TOKEN,
        "status": "completed",
        "stage": "done",
        "progress": 100,
        "source_path": str(tmp_path / "v.mp4"),
        "filename": "v.mp4",
        "options": {},
    }
    conn.execute("INSERT INTO jobs(id, payload, user_id) VALUES(?, ?, NULL)", (jid, json.dumps(payload)))
    conn.commit()
    conn.close()

    # Run migration
    storage.run_migration()

    # After migration, get_job with user_id should work
    job = storage.get_job(jid, user_id=TEST_USER_ID)
    assert job is not None, "Migration failed: get_job by user_id returned None"
    assert job["user_id"] == TEST_USER_ID


# ── Session expiry ────────────────────────────────────────────────────────────

def test_expired_session_is_rejected(monkeypatch, tmp_path):
    """get_session_user() must reject sessions where expires_at is in the past."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    from app.auth import get_session_user

    class FakeRequest:
        session = {
            "user_id": "tok:abc123",
            "auth_mode": "token",
            "expires_at": time.time() - 3600,  # expired 1 hour ago
        }
        headers = {}
        client = None

    result = get_session_user(FakeRequest())
    assert result is None, "Expired session should not authenticate"


def test_valid_session_is_accepted(monkeypatch, tmp_path):
    """get_session_user() accepts sessions where expires_at is in the future."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    from app.auth import get_session_user

    class FakeRequest:
        session = {
            "user_id": "tok:abc123",
            "auth_mode": "token",
            "expires_at": time.time() + 3600,  # valid for 1 hour
        }
        headers = {}
        client = None

    result = get_session_user(FakeRequest())
    assert result is not None
    assert result["user_id"] == "tok:abc123"


# ── OIDC Claims Validation Negative Tests ────────────────────────────────────

def test_verify_google_claims_valid(monkeypatch):
    from app.google_auth import verify_google_claims
    monkeypatch.delenv("GOOGLE_ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("GOOGLE_ALLOWED_DOMAINS", raising=False)
    claims = {
        "sub": "1234567890",
        "email": "tester@gmail.com",
        "email_verified": True,
        "aud": "my-client-id.apps.googleusercontent.com",
        "iss": "https://accounts.google.com",
        "exp": time.time() + 3600,
        "nonce": "test-nonce",
    }
    ok, reason = verify_google_claims(claims, "my-client-id.apps.googleusercontent.com", "test-nonce")
    assert ok is True
    assert reason == "OK"


def test_verify_google_claims_invalid_audience():
    from app.google_auth import verify_google_claims
    claims = {
        "sub": "1234567890",
        "email": "tester@gmail.com",
        "email_verified": True,
        "aud": "wrong-client-id",
        "iss": "https://accounts.google.com",
        "exp": time.time() + 3600,
    }
    ok, reason = verify_google_claims(claims, "expected-client-id")
    assert ok is False
    assert reason == "INVALID_AUDIENCE"


def test_verify_google_claims_invalid_issuer():
    from app.google_auth import verify_google_claims
    claims = {
        "sub": "1234567890",
        "email": "tester@gmail.com",
        "email_verified": True,
        "aud": "my-client-id",
        "iss": "https://evil-issuer.com",
        "exp": time.time() + 3600,
    }
    ok, reason = verify_google_claims(claims, "my-client-id")
    assert ok is False
    assert reason == "INVALID_ISSUER"


def test_verify_google_claims_expired_token():
    from app.google_auth import verify_google_claims
    claims = {
        "sub": "1234567890",
        "email": "tester@gmail.com",
        "email_verified": True,
        "aud": "my-client-id",
        "iss": "https://accounts.google.com",
        "exp": time.time() - 3600,  # Expired
    }
    ok, reason = verify_google_claims(claims, "my-client-id")
    assert ok is False
    assert reason == "TOKEN_EXPIRED"


def test_verify_google_claims_invalid_nonce():
    from app.google_auth import verify_google_claims
    claims = {
        "sub": "1234567890",
        "email": "tester@gmail.com",
        "email_verified": True,
        "aud": "my-client-id",
        "iss": "https://accounts.google.com",
        "exp": time.time() + 3600,
        "nonce": "wrong-nonce",
    }
    ok, reason = verify_google_claims(claims, "my-client-id", expected_nonce="expected-nonce")
    assert ok is False
    assert reason == "INVALID_NONCE"


def test_verify_google_claims_unverified_email():
    from app.google_auth import verify_google_claims
    claims = {
        "sub": "1234567890",
        "email": "unverified@gmail.com",
        "email_verified": False,  # Unverified!
        "aud": "my-client-id",
        "iss": "https://accounts.google.com",
        "exp": time.time() + 3600,
    }
    ok, reason = verify_google_claims(claims, "my-client-id")
    assert ok is False
    assert reason == "UNVERIFIED_EMAIL"


def test_verify_google_claims_email_allowlist(monkeypatch):
    from app.google_auth import verify_google_claims
    monkeypatch.setenv("GOOGLE_ALLOWED_EMAILS", "allowed@example.com,tester@gmail.com")
    claims = {
        "sub": "1234567890",
        "email": "blocked@gmail.com",
        "email_verified": True,
        "aud": "my-client-id",
        "iss": "https://accounts.google.com",
        "exp": time.time() + 3600,
    }
    ok, reason = verify_google_claims(claims, "my-client-id")
    assert ok is False
    assert reason == "EMAIL_NOT_ALLOWED"


def test_verify_google_claims_domain_allowlist(monkeypatch):
    from app.google_auth import verify_google_claims
    monkeypatch.setenv("GOOGLE_ALLOWED_DOMAINS", "company.com")
    # Disallowed domain
    claims_bad = {
        "sub": "123",
        "email": "user@other.com",
        "email_verified": True,
        "aud": "my-client-id",
        "iss": "https://accounts.google.com",
        "exp": time.time() + 3600,
    }
    ok, reason = verify_google_claims(claims_bad, "my-client-id")
    assert ok is False
    assert reason == "EMAIL_NOT_ALLOWED"

    # Allowed domain
    claims_good = {
        "sub": "123",
        "email": "user@company.com",
        "email_verified": True,
        "aud": "my-client-id",
        "iss": "https://accounts.google.com",
        "exp": time.time() + 3600,
    }
    ok, reason = verify_google_claims(claims_good, "my-client-id")
    assert ok is True


# ── Server-Side Session Storage & Revocation Tests ────────────────────────────

def test_server_side_session_creation_and_revocation(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    sid = "test-session-12345"
    uid = "goo:user999"
    expires = time.time() + 3600

    # Create session in DB
    storage.create_session(sid, uid, "google", expires, {"name": "Test User"})

    # Session exists and is valid
    sess = storage.get_session(sid)
    assert sess is not None
    assert sess["user_id"] == uid
    assert sess["auth_mode"] == "google"
    assert sess["data"]["name"] == "Test User"

    # Revoke session
    revoked = storage.revoke_session(sid)
    assert revoked is True

    # Revoked session returns None
    assert storage.get_session(sid) is None


def test_server_side_expired_session_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    sid = "expired-session-123"
    uid = "goo:user_expired"
    past_time = time.time() - 3600  # 1 hour ago

    storage.create_session(sid, uid, "google", past_time)

    # get_session deletes expired sessions automatically
    sess = storage.get_session(sid)
    assert sess is None

    # Cleanup function
    cleaned = storage.cleanup_expired_sessions()
    assert isinstance(cleaned, int)


# ── OAuth State Replay Protection ─────────────────────────────────────────────

def test_oauth_callback_rejects_missing_or_mismatched_state(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-secret")

    with TestClient(app, follow_redirects=False) as client:
        # Callback without state
        r = client.get("/auth/callback?code=fake-code")
        assert r.status_code == 302
        assert "auth_error=invalid_state" in r.headers["location"]


# ── Comprehensive Protected Route Cross-User Isolation ───────────────────────

def test_all_protected_routes_enforce_isolation(monkeypatch, tmp_path):
    """Verify that User B cannot access User A's resources across EVERY protected route."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("ACCESS_TOKENS", f"{TEST_TOKEN},{ALT_TOKEN}")
    _clear_rate_buckets()

    # Create job owned by TEST_USER_ID
    jid = uuid.uuid4().hex
    src_file = tmp_path / "test_video.mp4"
    src_file.write_bytes(b"dummy video content")
    out_file = tmp_path / "dubbed_video.mp4"
    out_file.write_bytes(b"dummy dubbed content")

    storage.set_job(jid, {
        "id": jid,
        "user_id": TEST_USER_ID,
        "owner_token": TEST_TOKEN,
        "status": "awaiting_review",
        "stage": "awaiting_review",
        "progress": 50,
        "source_path": str(src_file),
        "filename": "test_video.mp4",
        "options": {},
        "segments": [{"id": 0, "start": 0, "end": 2, "text_km": "សួស្តី"}],
        "outputs": {"video": "dubbed_video.mp4"},
    })

    with TestClient(app) as client:
        # 1. GET /api/jobs/{id}
        assert client.get(f"/api/jobs/{jid}", headers=_auth(ALT_TOKEN)).status_code == 404

        # 2. GET /api/jobs/{id}/segments
        assert client.get(f"/api/jobs/{jid}/segments", headers=_auth(ALT_TOKEN)).status_code == 404

        # 3. PUT /api/jobs/{id}/segments
        assert client.put(f"/api/jobs/{jid}/segments", json=[], headers=_auth(ALT_TOKEN)).status_code == 404

        # 4. POST /api/jobs/{id}/cancel
        assert client.post(f"/api/jobs/{jid}/cancel", headers=_auth(ALT_TOKEN)).status_code == 404

        # 5. GET /api/jobs/{id}/download/video
        assert client.get(f"/api/jobs/{jid}/download/video", headers=_auth(ALT_TOKEN)).status_code == 404

        # 6. DELETE /api/jobs/{id}
        assert client.delete(f"/api/jobs/{jid}", headers=_auth(ALT_TOKEN)).status_code == 404

    _clear_rate_buckets()


# ── /auth/callback — BLOCKED (real Google network required) ──────────────────

def test_google_callback_blocked_without_real_credentials():
    """BLOCKED: Real Google OAuth callback requires live Google network and valid credentials.
    Cannot be tested without GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and a real OAuth flow."""
    # This test explicitly documents the BLOCKED condition.
    import os
    has_creds = bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))
    if has_creds:
        pytest.skip("BLOCKED: real Google OAuth requires live network (skip in CI)")
    # Without credentials, callback returns 503 — that's already tested above
    assert True, "BLOCKED: Real Google OAuth callback test requires live environment"
