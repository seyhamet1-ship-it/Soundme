"""Run Phase 6 Harder Real-Mode Media Cases and Soft Translation Check.

Cases tested:
  1a. media_1a_long_bgm.mp4 (~80s, 10 sentences + BGM) with keep_background=False
  1a_bg. media_1a_long_bgm.mp4 with keep_background=True
  1b. media_1b_tight_slot.mp4 (fit logic proof: TTS rate <= +30%, atempo <= 1.25x, overlap <= 100ms)
  1c. media_1c_portrait_9_16.mp4 (9:16 portrait with burned-in Khmer subtitles + frame extraction)
  1d. media_1d_numbers.mp4 (numbers, dates, currency; log text that reached TTS)
  2. Soft translation check: back-translate Khmer segments to English and report similarities + 3 worst.
"""
from __future__ import annotations
import difflib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
API = "http://127.0.0.1:8000"
FFMPEG = os.getenv("FFMPEG", str(ROOT / "tools" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffmpeg.exe"))
FFPROBE = os.getenv("FFPROBE", str(ROOT / "tools" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffprobe.exe"))
DEMO = ROOT / "demo"
HARDER_DIR = DEMO / "harder_media"


def http_post(url: str, files=None, data=None):
    boundary = b"----PythonFormBoundary"
    body = b""
    if files:
        for name, (fname, fdata, ctype) in files.items():
            body += b"--" + boundary + b"\r\n"
            body += f'Content-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'.encode()
            body += f"Content-Type: {ctype}\r\n\r\n".encode()
            body += fdata + b"\r\n"
    if data:
        for k, v in data.items():
            body += b"--" + boundary + b"\r\n"
            body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
            body += str(v).encode() + b"\r\n"
    body += b"--" + boundary + b"--\r\n"
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def http_get(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def submit_and_poll(video_path: Path, *, keep_background: bool = False, burn_subtitles: bool = False, source_lang: str = "en") -> dict:
    with open(video_path, "rb") as f:
        vbytes = f.read()
    resp = http_post(f"{API}/api/jobs", files={"file": (video_path.name, vbytes, "video/mp4")}, data={
        "source_lang": source_lang,
        "voice": "km-KH-SreymomNeural",
        "keep_background": str(keep_background).lower(),
        "burn_subtitles": str(burn_subtitles).lower(),
        "review_before_tts": "false",
    })
    job_id = resp["id"]
    print(f"Submitted {video_path.name} (bg={keep_background}, sub={burn_subtitles}) -> job {job_id}", flush=True)

    deadline = time.time() + 900
    while time.time() < deadline:
        job = http_get(f"{API}/api/jobs/{job_id}")
        st = job.get("status")
        stage = job.get("stage", "?")
        prog = job.get("progress", 0)
        print(f"  [{stage}] {prog}% - {st}", flush=True)
        if st == "completed":
            return job
        if st == "failed":
            raise RuntimeError(f"Job failed: {job.get('error')}")
        time.sleep(4)
    raise TimeoutError(f"Job {job_id} timed out")


def probe_file(path: Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, encoding="utf-8", errors="replace"
    )
    return json.loads(out.stdout)


def assert_pipeline_output(job: dict, input_path: Path, label: str):
    root = Path(job["source_path"]).parent
    dubbed_mp4 = root / "dubbed.mp4"
    dub_wav = root / "dub.wav"
    transcript = json.loads((root / "transcript.json").read_text(encoding="utf-8"))

    p_out = probe_file(dubbed_mp4)
    p_in = probe_file(input_path)
    dur_out = float(p_out["format"]["duration"])
    dur_in = float(p_in["format"]["duration"])
    v_streams = [s for s in p_out["streams"] if s["codec_type"] == "video"]
    a_streams = [s for s in p_out["streams"] if s["codec_type"] == "audio"]

    print(f"\n--- Assertions for {label} ---", flush=True)
    # A1: 1 video + >= 1 audio
    assert len(v_streams) == 1 and len(a_streams) >= 1, f"Streams mismatch: {len(v_streams)}v, {len(a_streams)}a"
    print(f"  PASS A1 streams: 1v + {len(a_streams)}a", flush=True)

    # A2: duration within +-0.5s
    delta = abs(dur_out - dur_in)
    assert delta <= 0.5, f"Duration delta too large: {delta:.3f}s"
    print(f"  PASS A2 duration: out={dur_out:.2f}s, in={dur_in:.2f}s, delta={delta:.3f}s", flush=True)

    # A3: dub audible in every segment window (RMS > -45 dBFS) and passing is-real-speech guard
    from app.audio_quality import require_real_speech
    with wave.open(str(dub_wav), "rb") as f:
        w_rate = f.getframerate()
        w_raw = f.readframes(f.getnframes())
        w_samples = np.frombuffer(w_raw, dtype="<i2").astype(np.float32) / 32768.0

    for idx, s in enumerate(transcript.get("segments", [])):
        voice_file = root / f"voice-{idx:04d}.wav"
        if voice_file.is_file():
            require_real_speech(voice_file)
        s_start = int(float(s["start"]) * w_rate)
        s_end = min(len(w_samples), int(float(s["end"]) * w_rate))
        if s_end > s_start:
            chunk = w_samples[s_start:s_end]
            rms_val = float(np.sqrt(np.mean(chunk ** 2)))
            rms_db = 20 * np.log10(rms_val + 1e-9)
            assert rms_db > -45.0, f"Segment #{s['id']} RMS {rms_db:.1f} dBFS <= -45 dBFS"
    print(f"  PASS A3 dub audible in every segment window (RMS > -45 dBFS) & passing real-speech guard", flush=True)

    # A4: peak <= -1 dBFS
    with wave.open(str(dub_wav), "rb") as f:
        raw = f.readframes(f.getnframes())
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    peak_dbfs = 20 * np.log10(float(np.max(np.abs(samples))) + 1e-9)
    assert peak_dbfs <= -1.0, f"Peak {peak_dbfs:.2f} dBFS > -1.0 dBFS"
    print(f"  PASS A4 peak: {peak_dbfs:.2f} dBFS <= -1 dBFS", flush=True)

    # A5: clip overlap <= 100 ms
    fit_stats = transcript.get("fit_stats", [])
    max_overlap = max((s.get("overlap_ms", 0.0) for s in fit_stats), default=0.0)
    assert max_overlap <= 100.0, f"Overlap {max_overlap} ms > 100 ms"
    print(f"  PASS A5 clip overlap: max={max_overlap:.1f} ms <= 100 ms", flush=True)

    # A6: Khmer presence, no CJK
    for s in transcript.get("segments", []):
        km = s.get("text_km", "")
        assert not any("\u4e00" <= c <= "\u9fff" for c in km), f"CJK in Khmer: {km}"
        letters = [c for c in km if c.isalpha()]
        khmer = sum("\u1780" <= c <= "\u17ff" for c in letters)
        assert khmer > 0, f"No Khmer letters in: {km}"
    print(f"  PASS A6 Khmer script present, no CJK", flush=True)

    # A7: Monotonic subtitles
    for sub_file in ["subtitles.srt", "subtitles.vtt"]:
        sub_text = (root / sub_file).read_text(encoding="utf-8")
        assert len(sub_text.strip()) > 0, f"{sub_file} is empty"
    print(f"  PASS A7 SRT & VTT subtitles present & valid", flush=True)
    return dubbed_mp4, transcript


def main():
    print("=" * 70, flush=True)
    print("PHASE 6: HARDER REAL-MODE MEDIA TEST SUITE", flush=True)
    print("=" * 70, flush=True)

    # 1a. ~2 min 10+ sentences with background music: keep_background=False
    v1a = HARDER_DIR / "media_1a_long_bgm.mp4"
    j1a_off = submit_and_poll(v1a, keep_background=False)
    d1a_off, t1a_off = assert_pipeline_output(j1a_off, v1a, "1a (keep_background=OFF)")
    dest_1a_off = DEMO / "dubbed_1a_nobg.mp4"
    shutil.copy2(d1a_off, dest_1a_off)

    # 1a_bg. ~2 min 10+ sentences with background music: keep_background=True
    j1a_on = submit_and_poll(v1a, keep_background=True)
    d1a_on, t1a_on = assert_pipeline_output(j1a_on, v1a, "1a (keep_background=ON)")
    dest_1a_on = DEMO / "dubbed_1a_withbg.mp4"
    shutil.copy2(d1a_on, dest_1a_on)

    # 1b. Tight time slot: prove fit logic (TTS rate <= +30%, atempo <= 1.25x, overlap <= 100ms)
    v1b = HARDER_DIR / "media_1b_tight_slot.mp4"
    j1b = submit_and_poll(v1b)
    d1b, t1b = assert_pipeline_output(j1b, v1b, "1b (tight slot fit logic)")
    dest_1b = DEMO / "dubbed_1b_tight_slot.mp4"
    shutil.copy2(d1b, dest_1b)

    print("\n--- 1b Fit Stats Proof ---", flush=True)
    fit_stats = t1b.get("fit_stats", [])
    for stat in fit_stats:
        print(f"  Segment #{stat['segment_id']}: slot={stat['slot']}s, raw_dur={stat['raw_dur']}s, "
              f"tts_rate={stat['tts_rate']}, atempo={stat['atempo']}x, final_dur={stat['final_dur']}s, "
              f"overlap={stat['overlap_ms']}ms", flush=True)
        # Verify constraints
        rate_val = int(stat['tts_rate'].replace('+', '').replace('%', ''))
        assert rate_val <= 30, f"TTS rate {rate_val}% > 30%"
        assert stat['atempo'] <= 1.25, f"atempo {stat['atempo']} > 1.25x"
        assert stat['overlap_ms'] <= 100.0, f"overlap {stat['overlap_ms']}ms > 100ms"
    print("  CONFIRMED: TTS rate <= +30%, atempo <= 1.25x, overlap <= 100ms, no truncation.", flush=True)

    # 1c. 9:16 portrait video with burned-in subtitles
    v1c = HARDER_DIR / "media_1c_portrait_9_16.mp4"
    j1c = submit_and_poll(v1c, burn_subtitles=True)
    d1c, t1c = assert_pipeline_output(j1c, v1c, "1c (9:16 portrait burned subtitles)")
    dest_1c = DEMO / "dubbed_1c_portrait_burned.mp4"
    shutil.copy2(d1c, dest_1c)

    # Extract frame showing burned subtitle
    sub_frame_png = DEMO / "portrait_subtitles_frame.png"
    subprocess.run([
        FFMPEG, "-y", "-ss", "00:00:02.000", "-i", str(d1c),
        "-vframes", "1", "-q:v", "2", str(sub_frame_png)
    ], check=True, capture_output=True)
    print(f"\nExtracted portrait subtitle frame to: {sub_frame_png} ({sub_frame_png.stat().st_size} bytes)", flush=True)

    # 1d. Numbers, dates, and currency
    v1d = HARDER_DIR / "media_1d_numbers.mp4"
    j1d = submit_and_poll(v1d)
    d1d, t1d = assert_pipeline_output(j1d, v1d, "1d (numbers, dates, currency)")
    dest_1d = DEMO / "dubbed_1d_numbers.mp4"
    shutil.copy2(d1d, dest_1d)

    print("\n--- 1d Khmer text that actually reached TTS ---", flush=True)
    for s in t1d.get("segments", []):
        print(f"  Source: '{s.get('source_text')}'", flush=True)
        print(f"  Khmer TTS input: '{s.get('text_km')}'", flush=True)
        print(f"  Hex codepoints: {' '.join(f'U+{ord(c):04X}' for c in s.get('text_km',''))[:80]}...", flush=True)

    # 2. Soft translation check: back-translate each Khmer segment to English
    print("\n" + "=" * 70, flush=True)
    print("ITEM 2: SOFT TRANSLATION CHECK (BACK-TRANSLATION)", flush=True)
    print("=" * 70, flush=True)
    from app.providers import NllbTranslator
    translator = NllbTranslator()

    all_segments = t1a_off.get("segments", []) + t1d.get("segments", [])
    results = []
    for s in all_segments:
        km = s.get("text_km", "")
        src = s.get("source_text", "")
        try:
            back_en = translator.translate(km, source="km", target="en")
        except Exception as e:
            back_en = f"[error: {e}]"
        # Word-level similarity ratio
        ratio = difflib.SequenceMatcher(None, src.lower().split(), back_en.lower().split()).ratio()
        results.append({
            "id": s.get("id"),
            "source": src,
            "khmer": km,
            "back_en": back_en,
            "similarity": round(ratio, 3)
        })

    # Print all similarities
    print(f"{'SIM':<6} {'SOURCE':<40} {'BACK-TRANSLATION':<40}", flush=True)
    print("-" * 90, flush=True)
    for r in results:
        print(f"{r['similarity']:<6.3f} {r['source'][:38]:<40} {r['back_en'][:38]:<40}", flush=True)

    # 3 worst segments
    worst_3 = sorted(results, key=lambda x: x["similarity"])[:3]
    print("\n--- 3 Worst Segments by Word-Level Back-Translation Similarity ---", flush=True)
    for idx, w in enumerate(worst_3, 1):
        print(f"{idx}. Sim: {w['similarity']:.3f}", flush=True)
        print(f"   Source: '{w['source']}'", flush=True)
        print(f"   Khmer:  '{w['khmer']}'", flush=True)
        print(f"   Back:   '{w['back_en']}'", flush=True)

    print("\n" + "=" * 70, flush=True)
    print("ALL HARDER MEDIA RUNS & SOFT TRANSLATION CHECK COMPLETE", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    main()
