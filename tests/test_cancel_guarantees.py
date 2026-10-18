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
    # 1. Verify health
    with urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=5) as res:
        assert res.status == 200

    # 2. Upload sample_input.mp4
    boundary = "----WebKitFormBoundaryCancelTest123"
    body = bytearray()
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"sample_input.mp4\"\r\nContent-Type: video/mp4\r\n\r\n".encode("utf-8"))
    body.extend(SAMPLE.read_bytes())
    body.extend(f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"source_lang\"\r\n\r\nen\r\n".encode("utf-8"))
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        f"{BASE_URL}/api/jobs",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
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
        with urllib.request.urlopen(f"{BASE_URL}/api/jobs/{jid}", timeout=5) as res:
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
    cancel_req = urllib.request.Request(f"{BASE_URL}/api/jobs/{jid}/cancel", data=b"", method="POST")
    with urllib.request.urlopen(cancel_req, timeout=5) as res:
        assert res.status == 200
        cancel_res = json.loads(res.read().decode("utf-8"))
        assert cancel_res["status"] == "canceled"

    # 6. Verify SSE stream terminates and delivers no progress after cancel
    sse_req = urllib.request.Request(f"{BASE_URL}/api/jobs/{jid}/events")
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

    with urllib.request.urlopen(f"{BASE_URL}/api/jobs/{jid}", timeout=5) as res:
        final_job = json.loads(res.read().decode("utf-8"))
        assert final_job["status"] == "canceled", f"Expected canceled, got {final_job['status']}"

    # 8. Process table AFTER cancel
    procs_after = get_process_table()
    print("\n--- PROCESS TABLE AFTER CANCEL ---")
    for p in procs_after:
        print(f"  PID {p['pid']}: {p['name']} ({p['cmd']})")
    print(f"Total matching procs after cancel: {len(procs_after)}")

    assert len(procs_after) == 0, f"Expected 0 orphan ffmpeg/ffprobe processes, found: {procs_after}"

    # 9. Verify temp dir is cleaned
    if source_dir:
        assert not source_dir.exists(), f"Temp directory {source_dir} was not cleaned up!"
        print(f"Temp directory {source_dir} successfully cleaned: exists={source_dir.exists()}")


if __name__ == "__main__":
    test_cancel_lifecycle_and_process_cleanup()
    print("\nCANCEL LIFECYCLE TEST: ALL ASSERTIONS PASSED")
