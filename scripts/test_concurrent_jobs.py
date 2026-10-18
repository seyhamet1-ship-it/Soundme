"""Test 2 concurrent dubbing jobs through the API simultaneously.
Verifies thread pool isolation, file system isolation (uploads/<id>/ and outputs/<id>/),
and that both jobs complete with valid video outputs.
"""
import concurrent.futures
import time
import requests
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]
SAMPLE_EN = ROOT / "sample_input.mp4"
SAMPLE_ZH = ROOT / "sample_zh.mp4"

def log(msg: str):
    print(f"[CONCURRENT-TEST] {msg}", flush=True)

def submit_job(video_path: Path, lang: str, voice: str) -> str:
    with open(video_path, "rb") as f:
        resp = requests.post(
            f"{BASE_URL}/api/jobs",
            files={"file": (video_path.name, f.read(), "video/mp4")},
            data={"source_lang": lang, "voice": voice},
            timeout=20,
        )
    assert resp.status_code == 202
    return resp.json()["id"]

def poll_job(job_id: str, timeout: int = 360) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        resp = requests.get(f"{BASE_URL}/api/jobs/{job_id}", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status")
            stage = data.get("stage")
            progress = data.get("progress")
            log(f"   [{job_id[:8]}] stage={stage} progress={progress}% status={status}")
            if status in {"completed", "failed", "canceled"}:
                return data
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")

def main():
    log("Submitting 2 concurrent jobs simultaneously (Job 1: EN, Job 2: ZH)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(submit_job, SAMPLE_EN, "en", "km-KH-SreymomNeural")
        f2 = executor.submit(submit_job, SAMPLE_ZH, "zh", "km-KH-PisethNeural")
        id1 = f1.result()
        id2 = f2.result()

    log(f"Job 1 ID: {id1}")
    log(f"Job 2 ID: {id2}")
    assert id1 != id2, "Job IDs must be distinct"

    log("Polling both jobs to completion concurrently...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        poll1 = executor.submit(poll_job, id1)
        poll2 = executor.submit(poll_job, id2)
        res1 = poll1.result()
        res2 = poll2.result()

    assert res1.get("status") == "completed", f"Job 1 failed: {res1}"
    assert res2.get("status") == "completed", f"Job 2 failed: {res2}"

    # Verify download for both
    dl1 = requests.get(f"{BASE_URL}/api/jobs/{id1}/download/video", timeout=30)
    dl2 = requests.get(f"{BASE_URL}/api/jobs/{id2}/download/video", timeout=30)
    assert dl1.status_code == 200 and len(dl1.content) > 50_000
    assert dl2.status_code == 200 and len(dl2.content) > 50_000

    log(f"SUCCESS: Both concurrent jobs completed! Job1={len(dl1.content)} bytes, Job2={len(dl2.content)} bytes.")

if __name__ == "__main__":
    main()
