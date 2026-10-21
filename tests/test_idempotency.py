"""Phase 9 – updated test_idempotency.py with auth token headers."""
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("WARM_MODELS_AT_STARTUP", "0")
os.environ.setdefault("ACCESS_TOKENS", "soundme-test-token")

from app.main import app
from app import storage
from app.storage import get_idempotent_job, get_job

TOKEN = "soundme-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

SAMPLE = Path(__file__).resolve().parents[1] / "sample_input.mp4"


def _fake_probe(path):
    return {"streams": [{"codec_type": "video"}], "format": {"duration": "10"}}


def test_idempotency_key_duplicate_returns_same_job(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    from app import api
    monkeypatch.setattr(api, "probe", _fake_probe)
    monkeypatch.setattr(api.POOL, "submit", lambda *a, **kw: None)

    with TestClient(app) as client:
        key = f"test-idemp-{uuid.uuid4().hex}"
        with SAMPLE.open("rb") as f:
            res1 = client.post(
                "/api/jobs",
                files={"file": ("sample_input.mp4", f, "video/mp4")},
                data={"source_lang": "en", "voice": "km-KH-SreymomNeural"},
                headers={**AUTH, "Idempotency-Key": key},
            )
        assert res1.status_code == 202, res1.text
        jid = res1.json()["id"]

        with SAMPLE.open("rb") as f:
            res2 = client.post(
                "/api/jobs",
                files={"file": ("sample_input.mp4", f, "video/mp4")},
                data={"source_lang": "en", "voice": "km-KH-SreymomNeural"},
                headers={**AUTH, "Idempotency-Key": key},
            )
        assert res2.status_code == 202, res2.text
        job2 = res2.json()

        assert job2["id"] == jid
        assert job2["job_id"] == jid

        cached = get_idempotent_job(key)
        assert cached is not None
        assert cached["id"] == jid


def test_empty_file_rejected_with_khmer_error(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    empty_path = Path(__file__).resolve().parent / "fixtures" / "empty.mp4"
    with TestClient(app) as client:
        with empty_path.open("rb") as f:
            res = client.post(
                "/api/jobs",
                files={"file": ("empty.mp4", f, "video/mp4")},
                headers=AUTH,
            )
        assert res.status_code == 400
        detail = res.json().get("detail", "")
        assert "ឯកសារទទេ" in detail


def test_corrupt_file_rejected_with_khmer_error(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    corrupt_path = Path(__file__).resolve().parent / "fixtures" / "corrupt.mp4"
    with TestClient(app) as client:
        with corrupt_path.open("rb") as f:
            res = client.post(
                "/api/jobs",
                files={"file": ("corrupt.mp4", f, "video/mp4")},
                headers=AUTH,
            )
        assert res.status_code == 400
        detail = res.json().get("detail", "")
        assert "ទ្រង់ទ្រាយវីដេអូមិនត្រឹមត្រូវ" in detail
