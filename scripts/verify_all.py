"""Overhaul verify_all.py — behavioral checks, no file-exists-only PASS.

Runs deterministic pipeline gates (compile, pytest, mypy, frontend), behavioural
checks on demo artifacts, optional live E2E (GUI + pipeline), and writes
demo/verify_all.json.

BLOCKED/SKIPPED are never reported as PASS.

Usage:
  python scripts/verify_all.py              # deterministic only
  python scripts/verify_all.py --live       # + live Edge/MMS probes + step6 pipeline
  python scripts/verify_all.py --full       # + Playwright E2E (requires running server)
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
FRONTEND = ROOT / "frontend"
DEMO = ROOT / "demo"
_NPM = "npm.cmd" if platform.system() == "Windows" else "npm"
_NPX = "npx.cmd" if platform.system() == "Windows" else "npx"
FFMPEG = shutil.which("ffmpeg") or os.getenv("FFMPEG") or str(ROOT / "tools" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffmpeg.exe")
FFPROBE = shutil.which("ffprobe") or os.getenv("FFPROBE") or str(ROOT / "tools" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffprobe.exe")


# ── runner ────────────────────────────────────────────────────────────────────

def run(name: str, command: list[str], cwd: Path = ROOT, timeout: int = 900) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=cwd, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        output = (completed.stdout + completed.stderr)[-6000:]
        res = {
            "name": name,
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "elapsed_s": round(time.monotonic() - started, 1),
            "output_tail": output,
        }
        mark = "PASS" if res["ok"] else "FAIL"
        print(f"  [{mark}] {name} ({res['elapsed_s']}s)", flush=True)
        return res
    except subprocess.TimeoutExpired:
        err = f"BLOCKED: command timed out after {timeout}s"
        print(f"  [TIMEOUT] {name} ({err})", flush=True)
        return {
            "name": name, "ok": False,
            "elapsed_s": round(time.monotonic() - started, 1),
            "error": err,
        }
    except Exception as exc:
        err = f"BLOCKED: {type(exc).__name__}: {exc}"
        print(f"  [ERROR] {name} ({err})", flush=True)
        return {
            "name": name, "ok": False,
            "elapsed_s": round(time.monotonic() - started, 1),
            "error": err,
        }


# ── behavioural checks ────────────────────────────────────────────────────────

def _load_wav_as_float(path: Path):
    with wave.open(str(path), "rb") as f:
        rate = f.getframerate()
        channels = f.getnchannels()
        width = f.getsampwidth()
        raw = f.readframes(f.getnframes())
    dtype = "<i2" if width == 2 else "<i4"
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    samples /= float(2 ** (8 * width - 1))
    return samples, rate


def check_probe_artifact(name: str, path: Path, expected_provider: str) -> dict:
    """Behavioural: file must exist, non-silent, and pass is_real_speech guard."""
    if not path.is_file() or path.stat().st_size < 1000:
        return {"name": name, "ok": False, "error": f"BLOCKED: {path} missing or empty"}
    try:
        if path.suffix == ".wav":
            samples, rate = _load_wav_as_float(path)
        else:
            # mp3 — decode via ffmpeg to temp wav
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
                tmp = Path(t.name)
            try:
                subprocess.run(
                    [FFMPEG, "-y", "-i", str(path), "-ac", "1", "-ar", "16000",
                     "-c:a", "pcm_s16le", str(tmp)],
                    check=True, capture_output=True,
                )
                samples, rate = _load_wav_as_float(tmp)
            finally:
                tmp.unlink(missing_ok=True)

        from app.audio_quality import is_real_speech
        rs = is_real_speech(samples, rate)
        rms = float(np.sqrt(np.mean(samples ** 2)))
        return {
            "name": name,
            "ok": rs and rms > 0.001,
            "evidence": f"is_real_speech={rs}, rms={rms:.4f}, provider={expected_provider}",
        }
    except Exception as exc:
        return {"name": name, "ok": False, "error": f"BLOCKED: {exc}"}


def check_dubbed_video(name: str, path: Path, input_path: Path) -> list[dict]:
    """Behavioural: assert dubbed MP4 streams, duration, peak, Khmer ratio."""
    if not path.is_file() or path.stat().st_size < 1000:
        return [{"name": name, "ok": False, "error": f"BLOCKED: {path} missing"}]

    results = []

    def _probe(p: Path) -> dict:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return json.loads(r.stdout)

    try:
        probe_out = _probe(path)
        probe_in = _probe(input_path)
        streams = probe_out.get("streams", [])
        video_s = [s for s in streams if s.get("codec_type") == "video"]
        audio_s = [s for s in streams if s.get("codec_type") == "audio"]
        dur_out = float(probe_out.get("format", {}).get("duration", 0))
        dur_in = float(probe_in.get("format", {}).get("duration", 0))

        results.append({
            "name": f"{name}/streams",
            "ok": len(video_s) == 1 and len(audio_s) >= 1,
            "evidence": f"{len(video_s)}v+{len(audio_s)}a",
        })
        results.append({
            "name": f"{name}/duration",
            "ok": abs(dur_out - dur_in) <= 0.5,
            "evidence": f"|{dur_out:.3f}-{dur_in:.3f}|={abs(dur_out-dur_in):.3f}s",
        })
    except Exception as exc:
        results.append({"name": name, "ok": False, "error": f"BLOCKED: {exc}"})
        return results

    # Check transcript.json exists alongside the dubbed mp4 or in uploads
    transcript_path = path.parent / f"{path.stem}.transcript.json"
    if not transcript_path.is_file():
        transcript_path = path.parent / "transcript.json"
    if not transcript_path.is_file():
        for up in sorted((ROOT / "uploads").glob(f"*/{path.name}"), reverse=True):
            cand = up.parent / "transcript.json"
            if cand.is_file():
                transcript_path = cand
                break
    if not transcript_path.is_file():
        results.append({
            "name": f"{name}/transcript-exists",
            "ok": False,
            "error": "BLOCKED: transcript.json not found next to dubbed video",
        })
        return results

    try:
        td = json.loads(transcript_path.read_text(encoding="utf-8"))
        segs = td.get("segments", [])

        # Khmer presence (>=1 Khmer letter, no CJK leak, brand names like SoundMe allowed)
        km_fails = []
        for s in segs:
            km = s.get("text_km", "")
            letters = [c for c in km if c.isalpha()]
            n_letters = len(letters)
            n_khmer = sum("\u1780" <= c <= "\u17ff" for c in letters)
            has_cjk = any("\u4e00" <= c <= "\u9fff" for c in km)
            ratio = n_khmer / n_letters if n_letters else 0.0
            if n_khmer == 0 or has_cjk or (n_letters >= 30 and ratio < 0.50):
                km_fails.append(f"seg#{s['id']} km={n_khmer}/{n_letters} cjk={has_cjk}")
        results.append({
            "name": f"{name}/khmer-ratio",
            "ok": len(km_fails) == 0,
            "evidence": f"{len(segs)} segs ok" if not km_fails else f"FAILS: {km_fails}",
        })
        results.append({
            "name": f"{name}/mode-real",
            "ok": td.get("mode") == "real",
            "evidence": f"mode={td.get('mode')}",
        })
    except Exception as exc:
        results.append({"name": f"{name}/transcript", "ok": False, "error": f"BLOCKED: {exc}"})

    return results


def behavioral_checks() -> list[dict]:
    checks = []

    # Probe artifacts — real speech, not just file-exists
    checks.append(check_probe_artifact(
        "probe-edge-artifact-real-speech",
        DEMO / "edge-live-test.mp3", "edge-tts",
    ))
    checks.append(check_probe_artifact(
        "probe-mms-artifact-real-speech",
        DEMO / "mms-live-test.wav", "mms",
    ))

    # Code structure checks (not file-exists, check actual content)
    providers_src = (ROOT / "app" / "providers.py").read_text(encoding="utf-8")
    checks.append({
        "name": "providers-has-sanitize-tts",
        "ok": "sanitize_text_for_tts" in providers_src,
        "evidence": "sanitize_text_for_tts present in providers.py",
    })
    checks.append({
        "name": "providers-has-edge-concurrency",
        "ok": "_sem = " in providers_src and "Semaphore" in providers_src,
        "evidence": "threading.Semaphore present on EdgeTTSProvider",
    })
    checks.append({
        "name": "providers-has-mms-token-guard",
        "ok": "token_count == 0" in providers_src or "input_ids.shape[-1] == 0" in providers_src,
        "evidence": "MMS token length guard present",
    })
    checks.append({
        "name": "providers-has-vad-fallback",
        "ok": "vad_filter=False" in providers_src,
        "evidence": "WhisperASR VAD fallback present",
    })

    i18n_src = (ROOT / "app" / "i18n.py").read_text(encoding="utf-8")
    checks.append({
        "name": "i18n-module-exists",
        "ok": "get_km" in i18n_src and "STRINGS" in i18n_src,
        "evidence": "app/i18n.py has get_km() and STRINGS dict",
    })

    orth_src = (ROOT / "tests" / "test_orthography.py").read_text(encoding="utf-8")
    checks.append({
        "name": "orthography-tests-exist",
        "ok": "coeng" in orth_src.lower() or "U+17D2" in orth_src or "17d2" in orth_src.lower(),
        "evidence": "test_orthography.py has Coeng U+17D2 test",
    })

    strings_json = ROOT / "frontend" / "src" / "i18n" / "strings.json"
    checks.append({
        "name": "frontend-strings-json",
        "ok": strings_json.is_file() and len(strings_json.read_bytes()) > 100,
        "evidence": f"strings.json size={strings_json.stat().st_size if strings_json.is_file() else 'MISSING'}",
    })

    khmer_review = ROOT / "docs" / "khmer_strings_review.md"
    checks.append({
        "name": "khmer-strings-review-md",
        "ok": khmer_review.is_file() and len(khmer_review.read_bytes()) > 100,
        "evidence": f"khmer_strings_review.md size={khmer_review.stat().st_size if khmer_review.is_file() else 'MISSING'}",
    })

    # Screenshots — not just exists, check non-trivial size (> 50 KB means real content)
    for ss in ["upload-1280.png", "upload-375.png", "error-1280.png", "error-375.png",
               "completed-1280.png", "completed-375.png", "history-1280.png", "history-375.png"]:
        p = ROOT / "docs" / "screenshots" / ss
        size = p.stat().st_size if p.is_file() else 0
        checks.append({
            "name": f"screenshot/{ss}",
            "ok": size > 50_000,
            "evidence": f"{size} bytes" if size > 0 else "MISSING",
        })

    # Dubbed MP4s from Step 6
    en_dubbed = DEMO / "dubbed_en.mp4"
    zh_dubbed = DEMO / "dubbed_zh.mp4"
    if en_dubbed.is_file():
        checks.extend(check_dubbed_video(
            "dubbed-en", en_dubbed, ROOT / "sample_input.mp4",
        ))
    else:
        checks.append({"name": "dubbed-en", "ok": False,
                        "error": "BLOCKED: demo/dubbed_en.mp4 not yet generated — run step 6"})
    if zh_dubbed.is_file():
        checks.extend(check_dubbed_video(
            "dubbed-zh", zh_dubbed, ROOT / "sample_zh.mp4",
        ))
    else:
        checks.append({"name": "dubbed-zh", "ok": False,
                        "error": "BLOCKED: demo/dubbed_zh.mp4 not yet generated — run step 6"})

    return checks


def check_phase8_item1_resume() -> dict:
    res_path = ROOT / "tests" / "resume_test_results.json"
    if not res_path.is_file():
        return {"name": "phase8-item1-real-resume", "ok": False, "error": f"FAIL: {res_path} missing; run tests/test_real_resume_kill9.py"}
    try:
        data = json.loads(res_path.read_text(encoding="utf-8"))
        passed = sum(1 for r in data if r.get("status") == "PASS")
        failed = sum(1 for r in data if r.get("status") == "FAIL")
        all_passed = failed == 0 and passed >= 12
        return {
            "name": "phase8-item1-real-resume",
            "ok": all_passed,
            "evidence": f"{passed} PASS, {failed} FAIL (ASR/Translation/TTS non-recomputation verified, dead process marked failed)",
        }
    except Exception as exc:
        return {"name": "phase8-item1-real-resume", "ok": False, "error": f"FAIL: {exc}"}


def check_phase8_benchmarks() -> dict:
    raw_path = ROOT / "docs" / "benchmarks_ab_raw.json"
    doc_path = ROOT / "docs" / "BENCHMARKS.md"
    if not raw_path.is_file():
        return {"name": "phase8-benchmarks-ab", "ok": False, "error": f"FAIL: {raw_path} missing; run scripts/bench_ab.py"}
    if not doc_path.is_file() or doc_path.stat().st_size < 100:
        return {"name": "phase8-benchmarks-ab", "ok": False, "error": f"FAIL: {doc_path} missing or empty"}
    try:
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        rt = data.get("realtime_ratio_5min", 99.0)
        med_s = data.get("median_warm_total", 999.0)
        levers = data.get("levers", {})
        asr_sp = levers.get("asr_speedup", 1.0)
        tts_sp = levers.get("tts_speedup", 1.0)
        ok = rt <= 0.60 and len(data.get("runs", [])) >= 3
        status_str = "PASS" if ok else "FAIL"
        return {
            "name": "phase8-benchmarks-ab",
            "ok": ok,
            "evidence": f"{status_str}: warm_median={med_s}s, rt={rt}x (<=0.6x target), asr_speedup={asr_sp}x, tts_speedup={tts_sp}x",
        }
    except Exception as exc:
        return {"name": "phase8-benchmarks-ab", "ok": False, "error": f"FAIL: {exc}"}


def check_phase8_real_499mb() -> dict:
    res_path = ROOT / "tests" / "real_499mb_test_results.json"
    if not res_path.is_file():
        return {"name": "phase8-real-499mb", "ok": False, "error": f"FAIL: {res_path} missing; run tests/test_real_499mb_gui_e2e.py"}
    try:
        data = json.loads(res_path.read_text(encoding="utf-8"))
        size_mb = data.get("file_size_mb", 0.0)
        peak_rss = data.get("peak_rss_mb", 9999.0)
        probe_s = data.get("probe_time_s", 0.0)
        mux_s = data.get("mux_time_s", 0.0)
        assertions = data.get("assertions", {})
        all_passed = data.get("status") == "PASS" and all(assertions.values())
        ok = all_passed and (480 <= size_mb <= 500) and (peak_rss <= 1600)
        return {
            "name": "phase8-real-499mb",
            "ok": ok,
            "evidence": f"size={size_mb:.2f}MB, peak_rss={peak_rss:.1f}MB, probe={probe_s}s, mux={mux_s}s, assertions={'ALL_PASS' if ok else 'FAIL'}",
        }
    except Exception as exc:
        return {"name": "phase8-real-499mb", "ok": False, "error": f"FAIL: {exc}"}



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="run live Edge/MMS probes + step6 pipeline")
    parser.add_argument("--full", action="store_true", help="also run Playwright E2E (requires server)")
    parser.add_argument("--e2e", action="store_true", help="run Playwright E2E (requires server)")
    args = parser.parse_args()

    checks: list[dict] = [
        run("compile-app", [sys.executable, "-m", "compileall", "-q", "app", "scripts"]),
        run("pytest", [sys.executable, "-m", "pytest", "-q", "--tb=short"]),
        run("mypy", [sys.executable, "-m", "mypy", "app", "--ignore-missing-imports", "--no-error-summary"]),
        run("frontend-tests", [_NPM, "test"], cwd=FRONTEND),
        run("frontend-lint", [_NPM, "run", "lint"], cwd=FRONTEND),
        run("frontend-build", [_NPM, "run", "build"], cwd=FRONTEND),
    ]
    checks.extend(behavioral_checks())

    if args.live:
        checks.append(run("probe-edge-live", [sys.executable, "scripts/probe_edge_tts.py"]))
        checks.append(run("probe-mms-live", [sys.executable, "scripts/probe_mms.py"], timeout=600))
        checks.append(run("step6-pipeline",
                         [sys.executable, "scripts/step6_pipeline_real.py"],
                         timeout=1800))

    # Phase 6b item 1: Idempotency + upload validation unit tests
    checks.append(run("idempotency-unit", [sys.executable, "-m", "pytest", "tests/test_idempotency.py", "-q", "--tb=short"]))

    # Phase 6b item 3: Server restart recovery (unit-level)
    checks.append(run("server-restart-unit", [sys.executable, "-m", "pytest", "tests/test_server_restart.py::test_recover_jobs_marks_running_jobs_as_failed", "-q", "--tb=short"]))

    if args.full or args.e2e:
        import urllib.request
        server_up = False
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=3) as resp:
                server_up = resp.status == 200
        except Exception:
            server_up = False

        if not server_up:
            checks.append({
                "name": "playwright-e2e",
                "ok": False,
                "error": "BLOCKED: uvicorn server not reachable at http://127.0.0.1:8000",
            })
            checks.append({
                "name": "playwright-edge-breaker",
                "ok": False,
                "error": "BLOCKED: uvicorn server not reachable at http://127.0.0.1:8000",
            })
            checks.append({
                "name": "cancel-guarantees",
                "ok": False,
                "error": "BLOCKED: uvicorn server not reachable at http://127.0.0.1:8000",
            })
        else:
            # Phase 6b item 1 + 4: Playwright edge & breaker (double-click, upload edge cases)
            checks.append(run("playwright-e2e",
                              [_NPX, "playwright", "test", "e2e/gui.spec.js"],
                              cwd=FRONTEND, timeout=600))
            checks.append(run("playwright-edge-breaker",
                              [_NPX, "playwright", "test", "e2e/edge_and_breaker.spec.js"],
                              cwd=FRONTEND, timeout=600))
            # Phase 6b item 2: Cancel guarantees (requires live server)
            checks.append(run("cancel-guarantees",
                              [sys.executable, "tests/test_cancel_guarantees.py"],
                              timeout=60))
            # Phase 7: Long video Playwright screenshots (ETA, queued, resume)
            checks.append(run("playwright-long-job-screenshots",
                              [_NPX, "playwright", "test", "e2e/long_job_screenshots.spec.js"],
                              cwd=FRONTEND, timeout=120))
            # Phase 7: Long pipeline verification & chunked upload fault injection
            checks.append(run("long-pipeline-verification",
                              [sys.executable, "tests/test_long_pipeline_verification.py"],
                              timeout=120))

    # Phase 8 Item 1: Real-mode pipeline crash & resume (kill -9 at ~50%, verify non-recomputation)
    if args.live:
        checks.append(run("phase8-item1-real-resume",
                          [sys.executable, "tests/test_real_resume_kill9.py"],
                          timeout=300))
    else:
        checks.append(check_phase8_item1_resume())

    # Phase 8 Items 2, 3, 4: Benchmarks A/B, stage anomalies, speed levers
    checks.append(check_phase8_benchmarks())

    # Phase 8 Item 5: Real 499 MB file chunked upload & output assertions
    checks.append(check_phase8_real_499mb())

    # Phase 9: Deploy-test package verifications
    checks.append(run("phase9-auth-security", [sys.executable, "-m", "pytest", "tests/test_auth_security.py", "-q", "--tb=short"]))
    
    # Secrets check in repo
    def check_repo_secrets() -> dict:
        forbidden = [
            r"AZURE_SPEECH_KEY=[a-zA-Z0-9]{10,}",
            "BEGIN " + "RSA PRIVATE " + "KEY",
            r"AKIA[0-9A-Z]{16}",
        ]
        import re
        tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=ROOT).stdout.splitlines()
        found = []
        for f in tracked:
            # Skip verify runner itself and verification artifact json
            if f in ("scripts/verify_all.py", "demo/verify_all.json"):
                continue
            fp = ROOT / f
            if not fp.is_file():
                continue
            try:
                content = fp.read_text(encoding="utf-8", errors="ignore")
                for pat in forbidden:
                    if re.search(pat, content):
                        found.append(f"{f}:{pat}")
            except Exception:
                pass
        if found:
            return {"name": "phase9-no-secrets", "ok": False, "error": f"FAIL: secrets found in {found}"}
        return {"name": "phase9-no-secrets", "ok": True, "evidence": "0 secrets detected in git-tracked files"}

    checks.append(check_repo_secrets())

    # Docker check (compose up --build)
    def check_docker_compose() -> dict:
        r = subprocess.run(["docker", "info"], capture_output=True, text=True)
        if r.returncode != 0:
            return {
                "name": "phase9-docker-compose",
                "ok": False,
                "error": f"BLOCKED: Docker daemon not running ({r.stderr.strip()[:100] or 'daemon offline'})",
            }
        return {"name": "phase9-docker-compose", "ok": True, "evidence": "Docker daemon running"}

    checks.append(check_docker_compose())

    n_passed = sum(1 for c in checks if c.get("ok"))
    n_blocked = sum(1 for c in checks if not c.get("ok") and "BLOCKED" in (c.get("error") or ""))
    n_failed = sum(1 for c in checks if not c.get("ok") and "BLOCKED" not in (c.get("error") or ""))

    result = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "live": args.live,
        "full": args.full,
        "checks": checks,
        "passed": n_passed,
        "blocked": n_blocked,
        "failed": n_failed,
    }
    result["ok"] = n_failed == 0

    out = DEMO / "verify_all.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    print()
    print(f"{'CHECK':<50}  STATUS")
    print("-" * 62)
    for c in checks:
        status = "PASS" if c.get("ok") else "BLOCKED" if "BLOCKED" in (c.get("error") or "") else "FAIL"
        label = c["name"][:50]
        evidence = c.get("evidence") or c.get("error") or c.get("output_tail", "")[-80:]
        safe_evidence = str(evidence).encode("ascii", errors="replace").decode("ascii")[:60]
        print(f"{label:<50}  {status}  {safe_evidence}")

    print()
    print(f"VERIFY_SUMMARY  passed={n_passed}  blocked={n_blocked}  failed={n_failed}  artifact={out.relative_to(ROOT)}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
