"""Phase 9 Item 6 — Access control, rate-limit, isolation, and security tests.

All tests use FastAPI TestClient (no live server required).
BLOCKED items are marked explicitly — never faked as PASS.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import storage
from app.auth import _TOKEN_TIMESTAMPS, _IP_TIMESTAMPS, _LOCK

TEST_TOKEN = "soundme-test-token"
ALT_TOKEN = "soundme-alt-token"

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_input.mp4"


# ── helpers ───────────────────────────────────────────────────────────────────

def _auth(token: str = TEST_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _clear_rate_buckets() -> None:
    with _LOCK:
        _TOKEN_TIMESTAMPS.clear()
        _IP_TIMESTAMPS.clear()


def _fake_probe(path):
    return {"streams": [{"codec_type": "video"}], "format": {"duration": "10"}}


# ── 1. Unauthenticated → 401 ─────────────────────────────────────────────────

def test_unauthenticated_jobs_returns_401(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.get("/api/jobs")
        assert r.status_code == 401, r.text
        assert r.json()["detail"]["code"] == "UNAUTHORIZED"


def test_unauthenticated_upload_returns_401(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.post("/api/jobs", files={"file": ("test.mp4", b"fake", "video/mp4")})
        assert r.status_code == 401


def test_unauthenticated_voices_returns_401(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.get("/api/voices")
        assert r.status_code == 401


# ── 2. Wrong token → 401 ──────────────────────────────────────────────────────

def test_wrong_token_returns_401(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.get("/api/jobs", headers={"Authorization": "Bearer wrong-token-xyz"})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "INVALID_TOKEN"


def test_empty_bearer_returns_401(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.get("/api/jobs", headers={"Authorization": "Bearer "})
        assert r.status_code == 401


# ── 3. Health is public ───────────────────────────────────────────────────────

def test_health_is_public_no_token_needed(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200


# ── 4. Valid token → success ──────────────────────────────────────────────────

def test_valid_token_lists_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.get("/api/jobs", headers=_auth())
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def test_token_via_query_param(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.get(f"/api/jobs?token={TEST_TOKEN}")
        assert r.status_code == 200


# ── 5. Cross-token isolation ──────────────────────────────────────────────────

def test_cross_token_job_isolation(monkeypatch, tmp_path):
    """Token A cannot read Token B's job."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("ACCESS_TOKENS", f"{TEST_TOKEN},{ALT_TOKEN}")

    from app import api as api_mod
    with TestClient(app) as client:
        with patch.object(api_mod, "probe", side_effect=_fake_probe):
            with patch.object(api_mod.POOL, "submit", return_value=None):
                r = client.post(
                    "/api/jobs",
                    files={"file": ("v.mp4", b"fake", "video/mp4")},
                    headers=_auth(TEST_TOKEN),
                )
                assert r.status_code == 202, r.text
                job_id = r.json()["id"]

        # Token B reads Token A's job → 404
        r_b = client.get(f"/api/jobs/{job_id}", headers=_auth(ALT_TOKEN))
        assert r_b.status_code == 404, f"ISOLATION FAIL: token B got {r_b.status_code}"

        # Token A reads its own job → 200
        r_a = client.get(f"/api/jobs/{job_id}", headers=_auth(TEST_TOKEN))
        assert r_a.status_code == 200

        # Token B's job list must NOT include token A's job
        r_list = client.get("/api/jobs", headers=_auth(ALT_TOKEN))
        ids_b = [j["id"] for j in r_list.json()]
        assert job_id not in ids_b, "ISOLATION FAIL: job A visible in token B list"


def test_cross_token_download_isolation(monkeypatch, tmp_path):
    """Token B cannot download Token A's output."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("ACCESS_TOKENS", f"{TEST_TOKEN},{ALT_TOKEN}")

    from app import api as api_mod
    with TestClient(app) as client:
        with patch.object(api_mod, "probe", side_effect=_fake_probe):
            with patch.object(api_mod.POOL, "submit", return_value=None):
                r = client.post(
                    "/api/jobs",
                    files={"file": ("v.mp4", b"fake", "video/mp4")},
                    headers=_auth(TEST_TOKEN),
                )
                job_id = r.json()["id"]

        r_dl = client.get(f"/api/jobs/{job_id}/download/video", headers=_auth(ALT_TOKEN))
        assert r_dl.status_code == 404, "ISOLATION FAIL: token B can download token A's output"


def test_cross_token_segments_isolation(monkeypatch, tmp_path):
    """Token B cannot read Token A's segments."""
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("ACCESS_TOKENS", f"{TEST_TOKEN},{ALT_TOKEN}")

    jid = uuid.uuid4().hex
    storage.set_job(jid, {
        "id": jid, "owner_token": TEST_TOKEN,
        "status": "awaiting_review", "stage": "awaiting_review",
        "progress": 50, "source_path": str(tmp_path / "v.mp4"),
        "filename": "v.mp4", "options": {},
        "segments": [{"id": 0, "start": 0, "end": 5, "text_km": "test", "source_text": "hi"}],
    })

    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{jid}/segments", headers=_auth(ALT_TOKEN))
        assert r.status_code == 404, "ISOLATION FAIL: token B read token A's segments"


# ── 6. Rate limit → 429 with Retry-After ──────────────────────────────────────

def test_rate_limit_returns_429_with_retry_after(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("RATE_LIMIT_RPM", "3")
    _clear_rate_buckets()

    try:
        with TestClient(app) as client:
            for _ in range(3):
                client.get("/api/jobs", headers=_auth())
            r = client.get("/api/jobs", headers=_auth())
            assert r.status_code == 429, f"Expected 429, got {r.status_code}"
            assert "Retry-After" in r.headers
            assert int(r.headers["Retry-After"]) > 0
    finally:
        _clear_rate_buckets()
        monkeypatch.setenv("RATE_LIMIT_RPM", "120")


# ── 7. Queue cap → 429 with bilingual message ────────────────────────────────

def test_queue_cap_returns_429_bilingual(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("MAX_QUEUE_CAP", "2")

    from app import api as api_mod
    with patch("app.main.recover_jobs", return_value=None):
        with TestClient(app) as client:
            # Pre-insert queued jobs inside the TestClient block (after startup lifespan)
            for i in range(2):
                jid = uuid.uuid4().hex
                storage.set_job(jid, {
                    "id": jid, "owner_token": TEST_TOKEN,
                    "status": "queued", "stage": "queued", "progress": 0,
                    "source_path": str(tmp_path / "v.mp4"),
                    "filename": "v.mp4", "options": {},
                })
            with patch.object(api_mod, "probe", side_effect=_fake_probe):
                r = client.post(
                    "/api/jobs",
                    files={"file": ("v.mp4", b"fake", "video/mp4")},
                    headers=_auth(TEST_TOKEN),
                )
                assert r.status_code == 429, f"Expected 429 queue cap, got {r.status_code}"
                body = r.json()
                assert body["detail"]["code"] == "SERVER_BUSY"
                assert "message_en" in body["detail"]
                assert "message_km" in body["detail"]
                assert "Retry-After" in r.headers

    monkeypatch.setenv("MAX_QUEUE_CAP", "5")


# ── 8. Oversize declared size → 413 ──────────────────────────────────────────

def test_oversize_upload_init_returns_413(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.post(
            "/api/upload/init",
            json={"filename": "big.mp4", "total_size": 600 * 1024 * 1024, "chunk_size": 5 * 1024 * 1024},
            headers=_auth(),
        )
        assert r.status_code == 413, f"Expected 413, got {r.status_code}"


# ── 9. Path traversal → rejected or sanitized ────────────────────────────────

def test_path_traversal_in_upload_init_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.post(
            "/api/upload/init",
            json={"filename": "../../../etc/passwd", "total_size": 1024, "chunk_size": 1024},
            headers=_auth(),
        )
        assert r.status_code == 400, f"Expected 400 path traversal rejection, got {r.status_code}"


def test_path_traversal_in_job_filename_sanitized(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    from app import api as api_mod
    with TestClient(app) as client:
        with patch.object(api_mod, "probe", side_effect=_fake_probe):
            with patch.object(api_mod.POOL, "submit", return_value=None):
                r = client.post(
                    "/api/jobs",
                    files={"file": ("../../../etc/passwd", b"fake", "video/mp4")},
                    headers=_auth(TEST_TOKEN),
                )
                if r.status_code == 202:
                    job_data = storage.get_job(r.json()["id"])
                    fname = job_data.get("filename", "")
                    assert ".." not in fname
                    assert "/" not in fname
                    assert "\\" not in fname
                else:
                    assert r.status_code in {400, 422}


def test_invalid_download_kind_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    jid = uuid.uuid4().hex
    storage.set_job(jid, {
        "id": jid, "owner_token": TEST_TOKEN, "status": "completed",
        "source_path": str(tmp_path / "v.mp4"), "filename": "v.mp4", "options": {},
    })
    with TestClient(app) as client:
        # Try an invalid kind — should be rejected
        r = client.get(f"/api/jobs/{jid}/download/../../etc/passwd", headers=_auth())
        assert r.status_code in {400, 404, 422}


# ── 10. Security headers ──────────────────────────────────────────────────────

def test_security_headers_on_health(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"
        assert "strict-origin" in r.headers.get("Referrer-Policy", "")


def test_security_headers_on_401(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        r = client.get("/api/jobs")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"


# ── 11. No stack traces in error responses ────────────────────────────────────

def test_no_stack_trace_in_500_response(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with patch("app.storage.get_job", side_effect=RuntimeError("internal db error")):
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/jobs/nonexistent", headers=_auth())
            body = r.text
            assert "Traceback" not in body
            assert "File " not in body
            if r.status_code == 500:
                d = r.json()
                assert "code" in d or "detail" in d


# ── 12. Polling endpoint works (SSE fallback baseline) ────────────────────────

def test_polling_endpoint_returns_correct_job_data(monkeypatch, tmp_path):
    """Verify /api/jobs/{id} returns full job state for frontend polling fallback.

    BLOCKED: cannot block SSE at transport layer in TestClient.
    This test verifies the polling endpoint correctness only.
    """
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    jid = uuid.uuid4().hex
    # Pre-insert job BEFORE opening TestClient.
    # Use status="awaiting_review" so recover_jobs() does NOT attempt to re-queue it,
    # which would overwrite the status with "failed" (source file doesn't exist on disk).
    storage.set_job(jid, {
        "id": jid, "owner_token": TEST_TOKEN,
        "status": "awaiting_review", "stage": "awaiting_review", "progress": 50,
        "source_path": str(tmp_path / "v.mp4"), "filename": "v.mp4", "options": {},
    })

    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{jid}", headers=_auth())
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == jid
        assert data["status"] == "awaiting_review"
        assert data["progress"] == 50
    # BLOCKED: full SSE-blocked → polling fallback requires Playwright + SSE proxy


# ── 13. Daily quota exceeded → 429 ───────────────────────────────────────────

def test_daily_quota_exceeded_returns_429(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("DAILY_QUOTA_MINUTES", "1")

    # Record 1.5 min already used (exceeds 1-min quota)
    storage.add_daily_quota_used(TEST_TOKEN, 1.5)

    from app import api as api_mod
    with TestClient(app) as client:
        with patch.object(api_mod, "probe", side_effect=_fake_probe):
            r = client.post(
                "/api/jobs",
                files={"file": ("v.mp4", b"fake", "video/mp4")},
                headers=_auth(TEST_TOKEN),
            )
            assert r.status_code == 429, f"Expected 429 daily quota, got {r.status_code}"
            assert r.json()["detail"]["code"] == "DAILY_QUOTA_EXCEEDED"

    monkeypatch.setenv("DAILY_QUOTA_MINUTES", "60")
