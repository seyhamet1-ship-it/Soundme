import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
import pytest
import psutil

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_input.mp4"
BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
TEST_TOKEN = os.getenv("ACCESS_TOKENS", "soundme-test-token").split(",")[0].strip()
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


def get_process_table():
    """Returns list of active ffmpeg, ffprobe, and python worker subprocesses."""
    procs = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or [])
            if any(t in name for t in ["ffmpeg", "ffprobe"]):
                procs.append({"pid": p.info["pid"], "name": name, "cmd": cmd[:100]})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return procs


def test_cancel_lifecycle_and_process_cleanup():
    # 1. Verify health; skip if live server not running
    try:
        with urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=2) as res:
            if res.status != 200:
                pytest.skip(f"Server at {BASE_URL} not healthy")
    except Exception:
        pytest.skip(f"Live server not running at {BASE_URL}; run uvicorn to test live cancel")

    # 2. Upload sample_input.mp4
    boundary = "----WebKitFormBoundaryCancelTest123"
    body = bytearray()
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"sample_input.mp4\"\r\nContent-Type: video/mp4\r\n\r\n".encode("utf-8"))
    body.extend(SAMPLE.read_bytes())
    body.extend(f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"source_lang\"\r\n\r\nen\r\n".encode("utf-8"))
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    upload_headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", **AUTH_HEADERS}
    req = urllib.request.Request(
        f"{BASE_URL}/api/jobs",
        data=bytes(body),
        headers=upload_headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        assert res.status == 202
        job_data = json.loads(res.read().decode("utf-8"))
        jid = job_data["id"]

    # 3. Wait until job is running
    started = time.time()
    source_dir = None
    while time.time() - started < 10:
        status_req = urllib.request.Request(f"{BASE_URL}/api/jobs/{jid}", headers=AUTH_HEADERS)
        with urllib.request.urlopen(status_req, timeout=5) as res:
            cur = json.loads(res.read().decode("utf-8"))
            if cur.get("source_path"):
                source_dir = Path(cur["source_path"]).parent
            if cur.get("status") == "running":
                break
        time.sleep(0.3)

    # 4. Process table BEFORE cancel
    procs_before = get_process_table()
    print("\n--- PROCESS TABLE BEFORE CANCEL ---")
    for p in procs_before:
        print(f"  PID {p['pid']}: {p['name']} ({p['cmd']})")
    print(f"Total matching procs before cancel: {len(procs_before)}")

    # 5. Send cancel
    cancel_req = urllib.request.Request(f"{BASE_URL}/api/jobs/{jid}/cancel", data=b"", headers=AUTH_HEADERS, method="POST")
    with urllib.request.urlopen(cancel_req, timeout=5) as res:
        assert res.status == 200
        cancel_res = json.loads(res.read().decode("utf-8"))
        assert cancel_res["status"] == "canceled"

    # 6. Verify SSE stream terminates and delivers no progress after cancel
    sse_req = urllib.request.Request(f"{BASE_URL}/api/jobs/{jid}/events?token={TEST_TOKEN}", headers=AUTH_HEADERS)
    sse_events = []
    try:
        with urllib.request.urlopen(sse_req, timeout=3) as sse_stream:
            # SSE should yield the canceled event and close immediately
            for line in sse_stream:
                line_str = line.decode("utf-8").strip()
                if line_str.startswith("data:"):
                    data = json.loads(line_str[5:].strip())
                    sse_events.append(data)
    except Exception:
        pass  # stream closed as expected

    for ev in sse_events:
        assert ev.get("status") in {"running", "canceled"}
        if ev.get("status") == "canceled":
            # no events should follow canceled
            assert ev == sse_events[-1]

    # 7. Sleep 5 seconds and assert status STAYS canceled (never overwritten!)
    time.sleep(5.0)
    final_req = urllib.request.Request(f"{BASE_URL}/api/jobs/{jid}", headers=AUTH_HEADERS)
    with urllib.request.urlopen(final_req, timeout=5) as res:
        final_job = json.loads(res.read().decode("utf-8"))
        assert final_job["status"] == "canceled", f"Status regressed from canceled to {final_job['status']}"
        assert final_job["stage"] == "canceled"

    # 8. Assert source upload directory was cleaned up
    if source_dir:
        assert not source_dir.exists(), f"Source upload directory {source_dir} was NOT cleaned up on cancel"

    # 9. Assert ffmpeg/ffprobe subprocesses are terminated
    time.sleep(1.0)
    procs_after = get_process_table()
    print("--- PROCESS TABLE AFTER CANCEL ---")
    for p in procs_after:
        print(f"  PID {p['pid']}: {p['name']} ({p['cmd']})")
    print(f"Total matching procs after cancel: {len(procs_after)}")
    for p in procs_before:
        assert not psutil.pid_exists(p["pid"]), f"Process PID {p['pid']} ({p['name']}) survived cancel!"


if __name__ == "__main__":
    test_cancel_lifecycle_and_process_cleanup()
    print("ALL CANCEL GUARANTEE ASSERTIONS PASSED")
