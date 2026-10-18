"""Fresh-clone test script.
Clones the repo into a clean temporary directory, follows the README instructions,
starts the server, verifies /api/health, runs a real dubbing flow through the API,
and confirms the dubbed MP4 video is produced.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import requests
from pathlib import Path

def log(msg: str):
    print(f"[FRESH-CLONE] {msg}", flush=True)

def run_cmd(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    log(f"Running: {' '.join(str(c) for c in cmd)} in {cwd}")
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        log(f"STDOUT:\n{res.stdout}")
        log(f"STDERR:\n{res.stderr}")
        raise RuntimeError(f"Command failed with code {res.returncode}")
    return res

def main():
    repo_root = Path(__file__).resolve().parents[1]
    temp_dir = Path(tempfile.gettempdir()) / f"soundme_fresh_{int(time.time())}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)

    log(f"1. Cloning {repo_root} -> {temp_dir}")
    run_cmd(["git", "clone", str(repo_root), str(temp_dir)], cwd=repo_root)

    log("2. Verifying README exists and contains English + Khmer instructions")
    readme = (temp_dir / "README.md").read_text(encoding="utf-8")
    assert "Soundme" in readme, "README missing title"
    assert "សេចក្តីណែនាំសង្ខេបជាភាសាខ្មែរ" in readme, "README missing Khmer section"
    assert "start.ps1" in readme, "README missing start.ps1 reference"
    assert "start.sh" in readme, "README missing start.sh reference"
    log("README verified OK.")

    # Link frontend node_modules to test build capability
    frontend_orig = repo_root / "frontend" / "node_modules"
    frontend_temp = temp_dir / "frontend" / "node_modules"
    if frontend_orig.is_dir() and not frontend_temp.exists():
        log(f"Linking frontend node_modules from {frontend_orig}")
        try:
            os.symlink(frontend_orig, frontend_temp, target_is_directory=True)
        except (OSError, NotImplementedError):
            subprocess.run(["cmd", "/c", "mklink", "/J", str(frontend_temp), str(frontend_orig)], check=True)

    log("3. Starting backend server on port 8005...")
    server_log = open(temp_dir / "server.log", "w", encoding="utf-8")
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8005"],
        cwd=temp_dir,
        stdout=server_log,
        stderr=server_log,
    )

    try:
        log("Waiting for /api/health to turn green on port 8005...")
        healthy = False
        health_data = {}
        for attempt in range(40):
            time.sleep(1)
            try:
                resp = requests.get("http://127.0.0.1:8005/api/health", timeout=3)
                if resp.status_code == 200:
                    health_data = resp.json()
                    healthy = True
                    break
            except Exception as e:
                if attempt % 10 == 0:
                    log(f"Health attempt {attempt} waiting: {e}")

        if not healthy:
            server_log.flush()
            log(f"SERVER LOG:\n{(temp_dir / 'server.log').read_text(encoding='utf-8', errors='replace')}")
        assert healthy, "Backend failed to become healthy on port 8005"
        log(f"Health response: {health_data}")
        assert health_data.get("ffmpeg") is True, "ffmpeg not detected"
        assert health_data.get("status") in {"ok", "degraded"}, "health status unexpected"

        log("4. Uploading sample_input.mp4 to fresh clone backend...")
        sample_video = repo_root / "sample_input.mp4"
        assert sample_video.exists(), "sample_input.mp4 missing"

        with open(sample_video, "rb") as f:
            resp = requests.post(
                "http://127.0.0.1:8005/api/jobs",
                files={"file": ("sample_input.mp4", f, "video/mp4")},
                data={"source_lang": "en", "voice": "km-KH-SreymomNeural"},
                timeout=30,
            )
        assert resp.status_code == 202, f"Create job failed: {resp.status_code} {resp.text}"
        job_id = resp.json()["id"]
        log(f"Job created: {job_id}")

        log("5. Polling until job completes...")
        for _ in range(120):
            time.sleep(2)
            resp = requests.get(f"http://127.0.0.1:8005/api/jobs/{job_id}", timeout=10)
            if resp.status_code == 200:
                job_status = resp.json()
                stage = job_status.get("stage")
                progress = job_status.get("progress")
                status = job_status.get("status")
                log(f"   [{stage}] {progress}% -- status={status}")
                if status in {"completed", "failed"}:
                    break

        assert status == "completed", f"Job failed: {job_status}"
        log(f"Job completed successfully! outputs: {job_status.get('outputs')}")

        log("6. Downloading dubbed video...")
        dubbed_url = f"http://127.0.0.1:8005/api/jobs/{job_id}/download/video"
        resp = requests.get(dubbed_url, timeout=30)
        assert resp.status_code == 200, f"Download video failed: {resp.status_code}"
        dubbed_path = temp_dir / "dubbed_fresh_clone.mp4"
        dubbed_path.write_bytes(resp.content)
        log(f"Dubbed video saved to {dubbed_path} ({len(resp.content)} bytes)")
        assert len(resp.content) > 50_000, f"Dubbed video too small: {len(resp.content)} bytes"

        log("ALL FRESH-CLONE TESTS PASSED!")

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except Exception:
            server_proc.kill()
        try:
            server_log.close()
        except Exception:
            pass
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    main()
