import os
import subprocess
import sys
import time
import urllib.request
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pytest
from app.storage import set_job, get_job, recover_jobs, list_jobs
from app.i18n import STRINGS, get_km

TEST_TOKEN = "soundme-test-token"
AUTH_HEADER = {"Authorization": f"Bearer {TEST_TOKEN}"}


def test_recover_jobs_marks_running_jobs_as_failed():
    jid = "test-midjob-restart-123"
    set_job(
        jid,
        {
            "id": jid,
            "owner_token": TEST_TOKEN,
            "status": "running",
            "stage": "transcribe",
            "progress": 25,
            "filename": "sample_input.mp4",
        },
    )
    job_before = get_job(jid, owner_token=TEST_TOKEN)
    assert job_before is not None
    assert job_before["status"] == "running"

    # Simulate server restart lifecycle triggering recover_jobs()
    recover_jobs()

    job_after = get_job(jid, owner_token=TEST_TOKEN)
    assert job_after is not None
    assert job_after["status"] == "failed"
    assert job_after["stage"] == "failed"
    assert job_after["error"]["code"] == "SERVER_RESTARTED"
    assert job_after["error"]["message_km"] == STRINGS["server_restarted"].km
    assert job_after["error"]["message_en"] == STRINGS["server_restarted"].en
    assert "ម៉ាស៊ីនមេបានចាប់ផ្ដើមឡើងវិញ" in job_after["error"]["message_km"]


def test_real_server_kill_and_restart():
    """Spawns an isolated uvicorn server on a free port, creates a running job, kills it, restarts, and asserts recovery."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["ACCESS_TOKENS"] = TEST_TOKEN
    env["WARM_MODELS_AT_STARTUP"] = "0"

    # 1. Start isolated uvicorn
    p1 = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Wait for health
        up = False
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as res:
                    if res.status == 200:
                        up = True
                        break
            except Exception:
                time.sleep(0.5)
        assert up, f"Server failed to start on port {port}"

        # 2. Insert a simulated running job into storage
        sim_jid = "test-live-kill-job"
        set_job(
            sim_jid,
            {
                "id": sim_jid,
                "owner_token": TEST_TOKEN,
                "status": "running",
                "stage": "voice",
                "progress": 60,
                "filename": "video.mp4",
            },
        )

        # Verify it is reported as running by the live server
        req1 = urllib.request.Request(f"http://127.0.0.1:{port}/api/jobs/{sim_jid}", headers=AUTH_HEADER)
        with urllib.request.urlopen(req1) as res:
            live_job = json.loads(res.read().decode("utf-8"))
            assert live_job["status"] == "running"

        # 3. Force kill server (kill -9 equivalent on Windows)
        p1.kill()
        p1.wait()

        # 4. Restart server on same port
        p2 = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            up2 = False
            for _ in range(50):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as res:
                        if res.status == 200:
                            up2 = True
                            break
                except Exception:
                    time.sleep(0.5)
            assert up2, f"Restarted server failed to start on port {port}"

            # 5. Fetch job from restarted server
            req2 = urllib.request.Request(f"http://127.0.0.1:{port}/api/jobs/{sim_jid}", headers=AUTH_HEADER)
            with urllib.request.urlopen(req2) as res:
                recovered_job = json.loads(res.read().decode("utf-8"))
                assert recovered_job["status"] == "failed"
                assert recovered_job["error"]["code"] == "SERVER_RESTARTED"
                assert recovered_job["error"]["message_km"] == STRINGS["server_restarted"].km
                assert recovered_job["error"]["message_en"] == STRINGS["server_restarted"].en
                print(f"\nServer restart test recovered job: {recovered_job['error']}")
        finally:
            p2.kill()
            p2.wait()
    finally:
        if p1.poll() is None:
            p1.kill()
            p1.wait()


if __name__ == "__main__":
    test_recover_jobs_marks_running_jobs_as_failed()
    test_real_server_kill_and_restart()
    print("SERVER RESTART MID-JOB TEST: ALL ASSERTIONS PASSED")
