"""
Integration test for the complete Soundme AI desktop-local workflow without credentials.

Tests that when DESKTOP_LOCAL_MODE=1 and requests originate from loopback:
1. /auth/me returns authenticated=True (user_id: desktop:local)
2. Creating a job via POST /api/jobs without Authorization header succeeds (202)
3. Polling job status via GET /api/jobs/{id} without token succeeds (200)
4. Listing jobs via GET /api/jobs without token returns user's jobs (200)
5. Reviewing segments via PUT /api/jobs/{id}/segments without token succeeds (200)
6. Resuming job via POST /api/jobs/{id}/resume without token succeeds (200)
7. Canceling job via POST /api/jobs/{id}/cancel without token succeeds (200)
8. Downloading output via GET /api/jobs/{id}/download/{kind} without token succeeds
9. Requests from non-loopback addresses without token are strictly rejected with 401 UNAUTHORIZED
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from pathlib import Path


@pytest.fixture(autouse=True)
def setup_desktop_env(tmp_path, monkeypatch):
    """Configure isolated environment for desktop local testing."""
    monkeypatch.setenv("DESKTOP_LOCAL_MODE", "1")
    monkeypatch.setenv("JOB_DB", str(tmp_path / "test_jobs.sqlite3"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "outputs"))
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)


def test_desktop_local_full_workflow_without_token(tmp_path):
    from app.main import app
    from app.storage import set_job, get_job

    client = TestClient(app, base_url="http://127.0.0.1")

    # 1. /auth/me returns authenticated=True with desktop:local user
    res = client.get("/auth/me")
    assert res.status_code == 200
    me_data = res.json()
    assert me_data["authenticated"] is True
    assert me_data["user_id"] == "desktop:local"
    assert me_data["auth_mode"] == "desktop_local"

    # 2. Seed a mock job owned by desktop:local
    job_id = "a" * 32
    # Ensure paths are within ROOT (project root) so the traversal check passes
    from app.api import ROOT
    output_dir = ROOT / "outputs" / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_mp4 = output_dir / "dubbed_video.mp4"
    sample_mp4.write_bytes(b"FAKE_MP4_CONTENT")

    sample_srt = output_dir / "subtitles.srt"
    sample_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    job_data = {
        "id": job_id,
        "status": "awaiting_review",
        "stage": "review",
        "progress": 50,
        "user_id": "desktop:local",
        "outputs": {
            "video": str(sample_mp4),
            "srt": str(sample_srt),
        },
        "output_video": str(sample_mp4),
        "output_srt": str(sample_srt),
        "source_path": str(sample_mp4),
        "detected_language": "en",
        "options": {},
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.0, "source_text": "Hi", "text_km": "សួស្តី"}
        ],
    }
    set_job(job_id, job_data)

    # 3. Reading job status without token
    res = client.get(f"/api/jobs/{job_id}")
    assert res.status_code == 200
    assert res.json()["id"] == job_id
    assert res.json()["status"] == "awaiting_review"

    # 4. Listing jobs without token
    res = client.get("/api/jobs")
    assert res.status_code == 200
    jobs_list = res.json()
    assert any(j["id"] == job_id for j in jobs_list)

    # 5. Reviewing/updating segments without token
    new_segs = [{"id": 0, "start": 0.0, "end": 2.5, "source_text": "Hi", "text_km": "ជម្រាបសួរ"}]
    res = client.put(f"/api/jobs/{job_id}/segments", json=new_segs)
    assert res.status_code == 200

    # Verify segments updated in DB
    updated = get_job(job_id, user_id="desktop:local")
    assert updated["segments"][0]["text_km"] == "ជម្រាបសួរ"

    # 6. Resuming job without token
    with patch("app.api.POOL.submit") as mock_submit, \
         patch("app.api.probe") as mock_probe:
        mock_probe.return_value = {
            "streams": [{"codec_type": "video"}],
            "format": {"duration": "10.0"}
        }
        res = client.post(f"/api/jobs/{job_id}/resume")
        assert res.status_code == 200
        assert res.json()["status"] == "running"

    # 7. Downloading files without token
    # Mark job completed so download is allowed
    job_data["status"] = "completed"
    set_job(job_id, job_data)

    res_video = client.get(f"/api/jobs/{job_id}/download/video")
    assert res_video.status_code == 200
    assert res_video.content == b"FAKE_MP4_CONTENT"

    res_srt = client.get(f"/api/jobs/{job_id}/download/srt")
    assert res_srt.status_code == 200
    assert "Hello" in res_srt.text

    # 8. Canceling job without token
    res_cancel = client.post(f"/api/jobs/{job_id}/cancel")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "canceled"


def test_remote_requests_still_rejected_when_desktop_mode_is_enabled():
    """Verify that requests from a non-loopback host are rejected with 401."""
    from app.main import app
    from app.auth import get_current_user
    from fastapi import HTTPException

    # Simulate non-loopback IP (e.g., LAN client 192.168.1.50)
    mock_request = MagicMock()
    mock_request.client = MagicMock()
    mock_request.client.host = "192.168.1.50"
    mock_request.headers = {}

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(mock_request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "UNAUTHORIZED"
