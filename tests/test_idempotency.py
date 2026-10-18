import uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.storage import get_idempotent_job, get_job

client = TestClient(app)
SAMPLE = Path(__file__).resolve().parents[1] / "sample_input.mp4"


def test_idempotency_key_duplicate_returns_same_job():
    key = f"test-idemp-{uuid.uuid4().hex}"
    with SAMPLE.open("rb") as f:
        res1 = client.post(
            "/api/jobs",
            files={"file": ("sample_input.mp4", f, "video/mp4")},
            data={"source_lang": "en", "voice": "km-KH-SreymomNeural"},
            headers={"Idempotency-Key": key},
        )
    assert res1.status_code == 202
    job1 = res1.json()
    jid = job1["id"]

    # Immediate second post with same Idempotency-Key
    with SAMPLE.open("rb") as f:
        res2 = client.post(
            "/api/jobs",
            files={"file": ("sample_input.mp4", f, "video/mp4")},
            data={"source_lang": "en", "voice": "km-KH-SreymomNeural"},
            headers={"Idempotency-Key": key},
        )
    assert res2.status_code == 202
    job2 = res2.json()

    assert job2["id"] == jid
    assert job2["job_id"] == jid

    # Verify idempotency mapping stored in DB
    cached = get_idempotent_job(key)
    assert cached is not None
    assert cached["id"] == jid


def test_empty_file_rejected_with_khmer_error():
    empty_path = Path(__file__).resolve().parent / "fixtures" / "empty.mp4"
    with empty_path.open("rb") as f:
        res = client.post(
            "/api/jobs",
            files={"file": ("empty.mp4", f, "video/mp4")},
        )
    assert res.status_code == 400
    detail = res.json().get("detail", "")
    assert "ឯកសារទទេ" in detail


def test_corrupt_file_rejected_with_khmer_error():
    corrupt_path = Path(__file__).resolve().parent / "fixtures" / "corrupt.mp4"
    with corrupt_path.open("rb") as f:
        res = client.post(
            "/api/jobs",
            files={"file": ("corrupt.mp4", f, "video/mp4")},
        )
    assert res.status_code == 400
    detail = res.json().get("detail", "")
    assert "ទ្រង់ទ្រាយវីដេអូមិនត្រឹមត្រូវ" in detail
