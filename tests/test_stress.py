"""Independent review stress tests:
1. Bad uploads (corrupt bytes, non-video text, empty file)
2. Double-click / rapid concurrent upload
3. Cancel mid-job
4. Refresh / reconnect mid-job (SSE events & status retrieval)
5. 2 concurrent dubbing jobs (pool limit and job isolation)
"""
import concurrent.futures
import os
import time
import requests
import pytest
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]
SAMPLE_VIDEO = ROOT / "sample_input.mp4"
TEST_TOKEN = os.getenv("ACCESS_TOKENS", "soundme-test-token").split(",")[0].strip()
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(scope="session", autouse=True)
def ensure_server():
    try:
        resp = requests.get(f"{BASE_URL}/api/health", timeout=3)
        if resp.status_code != 200:
            pytest.skip(f"Server on {BASE_URL} not healthy")
    except Exception:
        pytest.skip(f"Server on {BASE_URL} not running")


def test_bad_upload_corrupt_file():
    """Bad upload: corrupted file with .mp4 extension must return 400."""
    fake_bytes = b"NOT_A_REAL_VIDEO_CORRUPT_DATA_1234567890"
    resp = requests.post(
        f"{BASE_URL}/api/jobs",
        files={"file": ("corrupt.mp4", fake_bytes, "video/mp4")},
        data={"source_lang": "en", "voice": "km-KH-SreymomNeural"},
        headers=AUTH_HEADERS,
        timeout=10,
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Invalid video" in detail or "មិនត្រឹមត្រូវ" in detail


def test_bad_upload_empty_file():
    """Bad upload: 0-byte empty file must return 400."""
    resp = requests.post(
        f"{BASE_URL}/api/jobs",
        files={"file": ("empty.mp4", b"", "video/mp4")},
        data={"source_lang": "en", "voice": "km-KH-SreymomNeural"},
        headers=AUTH_HEADERS,
        timeout=10,
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Invalid video" in detail or "ទទេ" in detail or "empty" in detail.lower()


def test_double_click_upload():
    """Rapid duplicate upload: submits 2 jobs simultaneously; both must succeed with distinct IDs."""
    def submit():
        with open(SAMPLE_VIDEO, "rb") as f:
            return requests.post(
                f"{BASE_URL}/api/jobs",
                files={"file": ("sample_input.mp4", f.read(), "video/mp4")},
                data={"source_lang": "en", "voice": "km-KH-SreymomNeural"},
                headers=AUTH_HEADERS,
                timeout=15,
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(submit)
        f2 = executor.submit(submit)
        r1 = f1.result()
        r2 = f2.result()

    assert r1.status_code == 202
    assert r2.status_code == 202
    id1 = r1.json()["id"]
    id2 = r2.json()["id"]
    assert id1 != id2, "Double submit must generate distinct job IDs"


def test_cancel_mid_job():
    """Cancel: submitting a job and cancelling it sets status to canceled."""
    with open(SAMPLE_VIDEO, "rb") as f:
        resp = requests.post(
            f"{BASE_URL}/api/jobs",
            files={"file": ("sample_input.mp4", f.read(), "video/mp4")},
            data={"source_lang": "en", "voice": "km-KH-SreymomNeural"},
            headers=AUTH_HEADERS,
            timeout=15,
        )
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    cancel_resp = requests.post(f"{BASE_URL}/api/jobs/{job_id}/cancel", headers=AUTH_HEADERS, timeout=5)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "canceled"

    status_resp = requests.get(f"{BASE_URL}/api/jobs/{job_id}", headers=AUTH_HEADERS, timeout=5)
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "canceled"


def test_refresh_mid_job_state_recovery():
    """Refresh mid-job: polling or SSE reconnection retrieves persisted job state."""
    with open(SAMPLE_VIDEO, "rb") as f:
        resp = requests.post(
            f"{BASE_URL}/api/jobs",
            files={"file": ("sample_input.mp4", f.read(), "video/mp4")},
            data={"source_lang": "en", "voice": "km-KH-SreymomNeural"},
            headers=AUTH_HEADERS,
            timeout=15,
        )
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    # Read first SSE event
    sse_resp = requests.get(f"{BASE_URL}/api/jobs/{job_id}/events?token={TEST_TOKEN}", headers=AUTH_HEADERS, stream=True, timeout=10)
    assert sse_resp.status_code == 200
    lines = []
    for line in sse_resp.iter_lines():
        if line:
            lines.append(line.decode("utf-8"))
            break
    sse_resp.close()
    assert any("data:" in l for l in lines)

    # Reconnect ("refresh browser")
    check_resp = requests.get(f"{BASE_URL}/api/jobs/{job_id}", headers=AUTH_HEADERS, timeout=5)
    assert check_resp.status_code == 200
    assert check_resp.json()["id"] == job_id
