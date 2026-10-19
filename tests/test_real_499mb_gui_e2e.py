"""Real 499 MB Video GUI E2E Test (Phase 8 Item 5).

Uploads realistic ~485-498 MB 1080p high-bitrate video with real Chinese speech
through the Web GUI via Playwright, monitors processing to completion,
records probe/mux times, disk use, peak RSS, and verifies all output assertions.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

FFMPEG = os.getenv("FFMPEG", "ffmpeg")
FFPROBE = os.getenv("FFPROBE", "ffprobe")


def run_499mb_gui_test():
    print("=" * 70)
    print("PHASE 8 ITEM 5: REAL 499 MB VIDEO GUI E2E TEST (PLAYWRIGHT)")
    print("=" * 70)

    # 1. Ensure realistic high-bitrate test video exists
    video_path = ROOT / "tests" / "benchmark" / "bench_real_498mb_zh.mp4"
    if not video_path.is_file():
        print("Generating real 498MB video...")
        from scripts.generate_real_499mb_video import generate_real_499mb_video
        video_path = generate_real_499mb_video()

    file_size_bytes = video_path.stat().st_size
    file_size_mb = file_size_bytes / (1024 * 1024)
    print(f"\n1. Source Video: {video_path}")
    print(f"   Size: {file_size_bytes:,} bytes ({file_size_mb:.2f} MB)")
    assert 480 <= file_size_mb <= 500, f"Video size {file_size_mb:.2f} MB out of realistic range (480-500 MB)"

    # Probe source
    probe_in = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video_path)],
        capture_output=True, text=True, check=True
    ).stdout)
    src_dur = float(probe_in["format"]["duration"])
    src_video = next(s for s in probe_in["streams"] if s["codec_type"] == "video")
    print(f"   Duration: {src_dur:.2f}s | Resolution: {src_video.get('width')}x{src_video.get('height')} @ {src_video.get('r_frame_rate')} fps")

    # 2. Track initial RSS
    proc = psutil.Process()
    initial_rss_mb = proc.memory_info().rss / (1024 * 1024)
    peak_rss_mb = initial_rss_mb

    print(f"\n2. Launching Playwright browser to upload through GUI (http://localhost:8000)...")
    from playwright.sync_api import sync_playwright

    t_upload_start = time.time()
    job_id = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        # Navigate to GUI
        page.goto("http://localhost:5173/", timeout=30000)
        # Clear any old state from localStorage and reload
        page.evaluate("() => localStorage.clear()")
        page.reload()
        page.wait_for_selector("h1", timeout=10000)

        # Select Chinese source language
        page.locator("select").first.select_option("zh")
        time.sleep(0.5)

        # Upload file through file input
        file_input = page.locator("input[type='file']")
        file_input.set_input_files(str(video_path))
        print("   File selected in dropzone. Waiting for Start button...")

        # Click Start Dubbing
        start_btn = page.locator("button.primary")
        start_btn.wait_for(state="visible", timeout=10000)
        start_btn.click()

        # Monitor upload and extract job_id from API response
        # Wait for localStorage activeJob or wait for job creation
        t_wait_job = time.time()
        while time.time() - t_wait_job < 120:
            jid_val = page.evaluate("() => localStorage.getItem('activeJob')")
            if jid_val:
                job_id = jid_val
                break
            time.sleep(1)
            # Track peak RSS
            current_rss = proc.memory_info().rss / (1024 * 1024)
            if current_rss > peak_rss_mb:
                peak_rss_mb = current_rss

        assert job_id, "Failed to retrieve active job_id from GUI after chunked upload"
        t_upload_duration = time.time() - t_upload_start
        print(f"   Upload complete! Job ID: {job_id} (Upload took {t_upload_duration:.1f}s)")

        # 3. Monitor pipeline processing through completion
        print("\n3. Monitoring pipeline processing...")
        job_dir = ROOT / "uploads" / job_id
        last_stage = None
        t_proc_start = time.time()

        while time.time() - t_proc_start < 600:
            current_rss = proc.memory_info().rss / (1024 * 1024)
            if current_rss > peak_rss_mb:
                peak_rss_mb = current_rss

            from app.storage import get_job
            job_data = get_job(job_id) or {}
            status = job_data.get("status")
            stage = job_data.get("stage")
            progress = job_data.get("progress", 0)

            if stage != last_stage:
                print(f"   Stage -> {stage} ({progress}%) [RSS: {current_rss:.1f} MB]")
                last_stage = stage

            if status == "completed":
                print(f"   Job {job_id} COMPLETED in {time.time() - t_proc_start:.1f}s!")
                break
            if status == "failed":
                err = job_data.get("error", {})
                raise RuntimeError(f"Job {job_id} failed: {err}")

            time.sleep(2)

        # Verify GUI surfaces completed state
        page.wait_for_selector(".dubbed-player, video", timeout=30000)
        browser.close()

    # 4. Extract measured stage timings, disk use, peak RSS
    print("\n4. Performance & Resource Measurements:")
    from app.storage import get_job
    job_data = get_job(job_id) or {}
    timings = job_data.get("timings", {})
    stg = timings.get("stage_times", {})
    counters = timings.get("counters", {})

    probe_time = stg.get("probe", 0.0)
    mux_time = stg.get("mux", 0.0)

    # Disk use of job folder
    disk_bytes = sum(f.stat().st_size for f in job_dir.rglob("*") if f.is_file())
    disk_mb = disk_bytes / (1024 * 1024)

    print(f"   Probe Time: {probe_time:.3f} s")
    print(f"   Mux Time:   {mux_time:.3f} s")
    print(f"   Disk Use:   {disk_mb:.2f} MB ({disk_bytes:,} bytes)")
    print(f"   Peak RSS:   {peak_rss_mb:.1f} MB (initial: {initial_rss_mb:.1f} MB)")
    print(f"   Counters:   ASR: {counters.get('asr_total')} segs, Translate: {counters.get('translate_total')}, TTS: {counters.get('tts_computed')} computed / {counters.get('tts_cached')} cached")

    # 5. Output Assertions on final dubbed MP4
    print("\n5. Output Assertions Verification:")
    dubbed_mp4 = job_dir / "dubbed.mp4"
    assert dubbed_mp4.is_file(), f"Output file missing: {dubbed_mp4}"
    out_size_mb = dubbed_mp4.stat().st_size / (1024 * 1024)
    print(f"   Output MP4: {dubbed_mp4} ({out_size_mb:.2f} MB)")

    # (a) Streams
    probe_out = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(dubbed_mp4)],
        capture_output=True, text=True, check=True
    ).stdout)
    out_streams = probe_out.get("streams", [])
    video_streams = [s for s in out_streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in out_streams if s.get("codec_type") == "audio"]
    print(f"   [PASS] OUTPUT_STREAMS: {len(video_streams)} video + {len(audio_streams)} audio")
    assert len(video_streams) == 1 and len(audio_streams) >= 1

    # (b) Duration delta
    out_dur = float(probe_out["format"]["duration"])
    dur_delta = abs(out_dur - src_dur)
    print(f"   [PASS] OUTPUT_DURATION_DELTA: Out={out_dur:.2f}s, In={src_dur:.2f}s, Delta={dur_delta:.3f}s <= 0.5s")
    assert dur_delta <= 0.5, f"Duration delta {dur_delta:.3f}s exceeds 0.5s limit"

    # (c) Real speech on audio track
    from app.audio_quality import is_real_speech
    dub_wav = job_dir / "dub.wav"
    assert dub_wav.is_file()
    with wave.open(str(dub_wav), "rb") as wf:
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
        audio_data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    speech_detected = is_real_speech(audio_data, rate)
    print(f"   [PASS] OUTPUT_REAL_SPEECH: is_real_speech={speech_detected}")
    assert speech_detected, "Audio quality assertion failed: real speech not detected"

    # (d) Peak level
    peak_val = float(np.max(np.abs(audio_data)))
    peak_db = 20 * np.log10(max(1e-5, peak_val))
    print(f"   [PASS] OUTPUT_PEAK_LEVEL: {peak_db:.2f} dBFS <= -1.0 dBFS")
    assert peak_db <= -1.0, f"Audio peak level {peak_db:.2f} dBFS exceeds -1.0 dBFS"

    # (e) Khmer script in transcript
    tr_path = job_dir / "transcript.json"
    assert tr_path.is_file()
    tr_data = json.loads(tr_path.read_text(encoding="utf-8"))
    segments = tr_data.get("segments", [])
    km_count = 0
    cjk_count = 0
    for s in segments:
        txt = s.get("text_km", "")
        if any('\u1780' <= c <= '\u17ff' for c in txt):
            km_count += 1
        if any('\u4e00' <= c <= '\u9fff' for c in txt):
            cjk_count += 1
    km_ratio = km_count / max(1, len(segments))
    print(f"   [PASS] OUTPUT_KHMER_SCRIPT: {km_count}/{len(segments)} segments Khmer ({km_ratio*100:.1f}% >= 90%), CJK={cjk_count}")
    assert km_ratio >= 0.90, f"Khmer script ratio {km_ratio:.2f} below 90%"
    assert cjk_count == 0, f"CJK characters leaked into translations: {cjk_count}"

    # (f) SRT monotonicity
    srt_path = job_dir / "subtitles.srt"
    assert srt_path.is_file()
    srt_content = srt_path.read_text(encoding="utf-8")
    timestamps = re.findall(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", srt_content)
    ts_seconds = [int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000 for h, m, s, ms in timestamps]
    start_times = ts_seconds[0::2]
    is_mono = all(start_times[i] <= start_times[i+1] for i in range(len(start_times)-1))
    print(f"   [PASS] OUTPUT_SRT_MONOTONIC: Monotonic timestamps across {len(start_times)} subtitles")
    assert is_mono, "SRT timestamps are not monotonic"

    print("\n" + "=" * 70)
    print("PHASE 8 ITEM 5 SUMMARY: ALL ASSERTIONS PASSED (100% REAL 499MB PIPELINE)")
    print("=" * 70)

    # Save summary artifact
    res_path = ROOT / "tests" / "real_499mb_test_results.json"
    res_data = {
        "status": "PASS",
        "file_size_bytes": file_size_bytes,
        "file_size_mb": round(file_size_mb, 2),
        "source_duration_s": round(src_dur, 2),
        "output_duration_s": round(out_dur, 2),
        "probe_time_s": probe_time,
        "mux_time_s": mux_time,
        "disk_mb": round(disk_mb, 2),
        "peak_rss_mb": round(peak_rss_mb, 1),
        "total_segments": len(segments),
        "assertions": {
            "streams": True,
            "duration_delta": True,
            "real_speech": True,
            "peak_level": True,
            "khmer_script": True,
            "no_cjk": True,
            "srt_monotonic": True
        }
    }
    res_path.write_text(json.dumps(res_data, indent=2), encoding="utf-8")
    return res_data


if __name__ == "__main__":
    run_499mb_gui_test()
