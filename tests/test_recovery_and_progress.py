"""Tests for startup recovery, queue capacity bounding, and health job_db exposure."""
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import set_job, get_job, list_jobs, update_job, recover_jobs


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    db_file = tmp_path / "test_jobs.sqlite3"
    monkeypatch.setenv("JOB_DB", str(db_file))
    from app import storage
    monkeypatch.setattr(storage, "DB", db_file)
    return db_file


def test_health_endpoint_exposes_job_db():
    client = TestClient(app)
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert "job_db" in data
    assert isinstance(data["job_db"], str)
    assert len(data["job_db"]) > 0


def test_recover_jobs_marks_orphaned_stale_running_job(temp_db, monkeypatch, tmp_path):
    # Create a dummy source file
    dummy_video = tmp_path / "stale.mp4"
    dummy_video.write_bytes(b"video-bytes")

    stale_time = time.time() - 3600 * 10  # 10 hours ago
    stale_job = {
        "id": "stale_running_01",
        "status": "running",
        "stage": "transcribe",
        "progress": 25,
        "started_at": stale_time,
        "source_path": str(dummy_video),
        "user_id": "desktop:local",
        "options": {},
    }
    set_job("stale_running_01", stale_job)

    # Set STALE_RUNNING_HOURS to 6
    monkeypatch.setenv("STALE_RUNNING_HOURS", "6")

    # Run recover_jobs
    with patch("app.api.POOL.submit"):
        recover_jobs()

    recovered = get_job("stale_running_01")
    assert recovered["status"] == "failed"
    assert recovered["error"]["code"] == "ORPHANED_WORKER"


def test_recover_jobs_marks_missing_source_as_failed(temp_db, monkeypatch):
    missing_job = {
        "id": "missing_src_01",
        "status": "running",
        "stage": "transcribe",
        "progress": 25,
        "started_at": time.time(),
        "source_path": "C:/nonexistent/path/video.mp4",
        "user_id": "desktop:local",
        "options": {},
    }
    set_job("missing_src_01", missing_job)

    with patch("app.api.POOL.submit"):
        recover_jobs()

    recovered = get_job("missing_src_01")
    assert recovered["status"] == "failed"
    assert recovered["error"]["code"] == "SERVER_RESTARTED"


def test_recover_jobs_respects_queue_cap(temp_db, monkeypatch, tmp_path):
    # Set cap to 2
    monkeypatch.setenv("MAX_QUEUE_CAP", "2")
    monkeypatch.setenv("STALE_RUNNING_HOURS", "24")

    # Create 4 jobs with valid source files
    dummy_video = tmp_path / "valid.mp4"
    dummy_video.write_bytes(b"valid-video-bytes")

    now = time.time()
    for i in range(4):
        jid = f"cap_job_0{i}"
        set_job(jid, {
            "id": jid,
            "status": "queued",
            "stage": "resuming",
            "progress": 10,
            "started_at": now,
            "source_path": str(dummy_video),
            "user_id": "desktop:local",
            "options": {},
        })

    submitted = []
    with patch("app.api.POOL.submit", side_effect=lambda fn, *args: submitted.append(args[0])):
        recover_jobs()

    # Only 2 jobs should have been submitted to POOL
    assert len(submitted) == 2

    # The remaining jobs exceeding cap should be marked failed
    failed_count = sum(1 for i in range(4) if get_job(f"cap_job_0{i}")["status"] == "failed")
    assert failed_count == 2
