"""End-to-end pipeline verification runs.

Modes:
  real         full ASR -> NLLB -> Edge/Azure/MMS TTS -> mux on a generated sample video
  demo         explicit DEMO_MODE plumbing run: fixture translation + labeled demo tone
  unavailable  TTS_PROVIDER=none run must fail closed with error.code TTS_UNAVAILABLE

Usage: python scripts/run_e2e.py <real|demo|unavailable>
Writes demo/e2e_<mode>_result.json and prints a single E2E_RESULT line.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODE_ENV = {
    "real": {"DEMO_MODE": "", "TTS_PROVIDER": "auto", "TRANSLATION_PROVIDER": "nllb"},
    "demo": {"DEMO_MODE": "1", "TTS_PROVIDER": "demo", "TRANSLATION_PROVIDER": "demo"},
    "unavailable": {"DEMO_MODE": "1", "TTS_PROVIDER": "none", "TRANSLATION_PROVIDER": "demo"},
}


def ensure_sample() -> Path:
    """Reuse sample_input.mp4 or generate it with real English Edge speech over a test pattern."""
    sample = ROOT / "sample_input.mp4"
    if sample.is_file() and sample.stat().st_size > 0:
        return sample
    subprocess.run(
        [sys.executable, str(ROOT / "sample_generate.py")], cwd=ROOT, check=True, capture_output=True, text=True
    )
    if not sample.is_file():
        raise RuntimeError("sample_input.mp4 generation failed")
    return sample


def poll(client, job_id: str, timeout_s: float) -> tuple[dict, list[dict]]:
    started = time.monotonic()
    timeline: list[dict] = []
    last = ""
    while time.monotonic() - started < timeout_s:
        job = client.get(f"/api/jobs/{job_id}").json()
        marker = f"{job.get('status')}:{job.get('stage')}:{job.get('progress')}"
        if marker != last:
            timeline.append({"elapsed_s": round(time.monotonic() - started, 1), "status": job.get("status"), "stage": job.get("stage"), "progress": job.get("progress")})
            last = marker
        if job.get("status") in {"completed", "failed", "canceled"}:
            return job, timeline
        time.sleep(1.5)
    raise TimeoutError(f"job {job_id} did not finish in {timeout_s}s")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in MODE_ENV:
        print("usage: run_e2e.py <real|demo|unavailable>", file=sys.stderr)
        return 2
    mode = sys.argv[1]
    for key, value in MODE_ENV[mode].items():
        if value == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    # Azure stays opt-in; clear ambient credentials so behavior is deterministic.
    os.environ.pop("AZURE_SPEECH_KEY", None)
    os.environ.pop("AZURE_SPEECH_REGION", None)

    from fastapi.testclient import TestClient

    from app.main import app

    result: dict = {"mode": mode, "env": MODE_ENV[mode], "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    started = time.monotonic()
    try:
        with TestClient(app) as client:
            result["health"] = client.get("/api/health").json()
            sample = ensure_sample()
            with sample.open("rb") as handle:
                response = client.post(
                    "/api/jobs",
                    files={"file": (sample.name, handle, "video/mp4")},
                    data={"source_lang": "en", "voice": "km-KH-SreymomNeural"},
                )
            if response.status_code != 202:
                raise RuntimeError(f"upload failed: {response.status_code} {response.text}")
            job_id = response.json()["id"]
            result["job_id"] = job_id
            job, timeline = poll(client, job_id, timeout_s=900)
            result["timeline"] = timeline
            result["job"] = job
            folder = ROOT / "uploads" / job_id
            result["artifacts"] = {p.name: p.stat().st_size for p in sorted(folder.iterdir()) if p.is_file()}
            transcript = folder / "transcript.json"
            if transcript.is_file():
                result["transcript"] = json.loads(transcript.read_text(encoding="utf-8"))
            result["downloads"] = {
                kind: client.get(f"/api/jobs/{job_id}/download/{kind}").status_code
                for kind in ("video", "audio", "srt", "vtt", "json")
            }

        if mode == "real":
            ok = (
                job.get("status") == "completed"
                and result["transcript"].get("mode") == "real"
                and all(str(p).startswith(("edge-tts", "azure", "mms-")) for p in result["transcript"].get("tts_providers", []))
                and set(result["downloads"].values()) == {200}
            )
        elif mode == "demo":
            ok = (
                job.get("status") == "completed"
                and result["transcript"].get("mode") == "demo-plumbing"
                and all(p == "demo-tone-not-speech" for p in result["transcript"].get("tts_providers", []))
                and all(p == "demo-fixture" for p in result["transcript"].get("translation_providers", []))
            )
        else:
            ok = job.get("status") == "failed" and (job.get("error") or {}).get("code") == "TTS_UNAVAILABLE"
        result["ok"] = bool(ok)
    except Exception as exc:
        result["ok"] = False
        result["exception"] = f"{type(exc).__name__}: {exc}"
    result["elapsed_s"] = round(time.monotonic() - started, 1)
    result["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = ROOT / "demo" / f"e2e_{mode}_result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    error = (result.get("job") or {}).get("error")
    print(
        f"E2E_RESULT={'PASS' if result['ok'] else 'FAIL'} mode={mode} "
        f"status={(result.get('job') or {}).get('status')} "
        f"error_code={(error or {}).get('code', '-')} elapsed_s={result['elapsed_s']} artifact={out.relative_to(ROOT)}"
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

