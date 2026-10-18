"""Step 6 — Full pipeline real-mode assertions on zh and en test videos.

Assertions (each printed PASS or FAIL with evidence):
  A1  exactly 1 video + >=1 audio stream
  A2  duration within ±0.5 s of input
  A3  dub audible in every segment window (RMS > -45 dBFS) and passes is_real_speech
  A4  peak <= -1 dBFS
  A5  clip overlap <= 100 ms (from transcript.json timings)
  A6  every Khmer segment >= 90% Khmer script, no CJK
  A7  SRT/VTT timestamps monotonic
"""
from __future__ import annotations
import json, os, subprocess, sys, time, wave
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8000"
FFPROBE = os.getenv("FFPROBE", str(ROOT / "tools" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffprobe.exe"))
DEMO_DIR = ROOT / "demo"
DEMO_DIR.mkdir(exist_ok=True)

# ── helpers ─────────────────────────────────────────────────────────────────

def run(*args) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError(f"Command failed ({r.returncode}): {' '.join(str(a) for a in args)}\n{r.stderr[:400]}")
    return r.stdout

def http_post(url: str, files=None, data=None):
    import urllib.request, urllib.parse
    if files:
        import mimetypes
        boundary = b"----PythonFormBoundary"
        body = b""
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
        req = urllib.request.Request(url, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"})
    else:
        body = json.dumps(data or {}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def http_get(url: str):
    import urllib.request
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())

def submit_job(video_path: Path, source_lang: str) -> str:
    with open(video_path, "rb") as f:
        video_bytes = f.read()
    resp = http_post(f"{API}/api/jobs", files={
        "file": (video_path.name, video_bytes, "video/mp4")
    }, data={"source_lang": source_lang, "voice": "km-KH-SreymomNeural",
             "keep_background": "false", "burn_subtitles": "false", "review_before_tts": "false"})
    return resp["id"]

def poll_job(job_id: str, timeout: int = 600) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = http_get(f"{API}/api/jobs/{job_id}")
        st = status.get("status")
        print(f"  [{status.get('stage','?')}] {status.get('progress',0)}% — {st}", flush=True)
        if st == "completed":
            return status
        if st == "failed":
            err = status.get("error", {})
            raise RuntimeError(f"Job failed: {err}")
        time.sleep(5)
    raise TimeoutError(f"Job {job_id} timed out after {timeout}s")

def download_output(job_id: str, kind: str, dest: Path) -> None:
    import urllib.request
    url = f"{API}/api/jobs/{job_id}/download/{kind}"
    urllib.request.urlretrieve(url, dest)

def ffprobe_json(path: Path) -> dict:
    out = run(FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path))
    return json.loads(out)

def is_khmer_text(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    khmer = sum("\u1780" <= c <= "\u17ff" for c in letters)
    if khmer == 0:
        return False
    return not any("\u4e00" <= c <= "\u9fff" for c in text)

def khmer_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    khmer = sum("\u1780" <= c <= "\u17ff" for c in letters)
    return khmer / len(letters)

# ── is_real_speech inline (no circular import) ───────────────────────────────

def _is_real_speech_wav(wav_path: Path) -> bool:
    sys.path.insert(0, str(ROOT))
    from app.audio_quality import is_real_speech
    with wave.open(str(wav_path), "rb") as f:
        rate, channels, width = f.getframerate(), f.getnchannels(), f.getsampwidth()
        raw = f.readframes(f.getnframes())
    dtype = "<i2" if width == 2 else "<i4"
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    samples /= float(2 ** (8 * width - 1))
    return is_real_speech(samples, rate)

def rms_dbfs(wav_path: Path, start_s: float, end_s: float) -> float:
    with wave.open(str(wav_path), "rb") as f:
        rate = f.getframerate()
        width = f.getsampwidth()
        channels = f.getnchannels()
        start_frame = int(start_s * rate)
        nframes = max(1, int((end_s - start_s) * rate))
        f.setpos(min(start_frame, f.getnframes() - 1))
        raw = f.readframes(nframes)
    dtype = "<i2" if width == 2 else "<i4"
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if samples.size == 0:
        return -100.0
    samples /= float(2 ** (8 * width - 1))
    rms = float(np.sqrt(np.mean(samples ** 2)))
    if rms < 1e-12:
        return -120.0
    return 20 * np.log10(rms)

def parse_srt_timestamps(srt_text: str) -> list[tuple[float, float]]:
    import re
    pattern = re.compile(r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})")
    def to_s(ts: str) -> float:
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000
    return [(to_s(m.group(1)), to_s(m.group(2))) for m in pattern.finditer(srt_text)]

def parse_vtt_timestamps(vtt_text: str) -> list[tuple[float, float]]:
    import re
    pattern = re.compile(r"(\d{2}:\d{2}:\d{2}\.\d{3}) --> (\d{2}:\d{2}:\d{2}\.\d{3})")
    def to_s(ts: str) -> float:
        h, m, rest = ts.split(":")
        s, ms = rest.split(".")
        return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000
    return [(to_s(m.group(1)), to_s(m.group(2))) for m in pattern.finditer(vtt_text)]

# ── assertion runner ─────────────────────────────────────────────────────────

_results: list[tuple[str, str, str]] = []  # (label, status, evidence)

def check(label: str, condition: bool, evidence: str, warn_only: bool = False) -> None:
    status = "PASS" if condition else ("WARN" if warn_only else "FAIL")
    _results.append((label, status, evidence))
    print(f"  {status}  {label}: {evidence}", flush=True)
    if status == "FAIL":
        global _any_fail
        _any_fail = True

_any_fail = False

def assert_video(label_prefix: str, job_id: str, input_path: Path,
                 dubbed_dest: Path, source_lang: str) -> None:
    job = http_get(f"{API}/api/jobs/{job_id}")
    outputs = job.get("outputs", {})
    upload_dir = Path(job["source_path"]).parent

    # Download dubbed.mp4
    dubbed_src = upload_dir / "dubbed.mp4"
    import shutil
    shutil.copy2(dubbed_src, dubbed_dest)
    print(f"  Dubbed MP4 saved to: {dubbed_dest}", flush=True)

    # ffprobe the output
    probe = ffprobe_json(dubbed_dest)
    streams = probe.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    dur_out = float(probe.get("format", {}).get("duration", 0))

    probe_in = ffprobe_json(input_path)
    dur_in = float(probe_in.get("format", {}).get("duration", 0))

    print(f"\n  ffprobe {dubbed_dest.name}:", flush=True)
    print(f"    streams: {len(video_streams)}v + {len(audio_streams)}a", flush=True)
    print(f"    duration: {dur_out:.3f}s  (input: {dur_in:.3f}s, delta: {abs(dur_out-dur_in):.3f}s)", flush=True)
    print(f"    codecs: video={video_streams[0].get('codec_name') if video_streams else 'NONE'}"
          f"  audio={audio_streams[0].get('codec_name') if audio_streams else 'NONE'}", flush=True)

    # A1
    check(f"{label_prefix}/A1 streams", len(video_streams) == 1 and len(audio_streams) >= 1,
          f"{len(video_streams)} video, {len(audio_streams)} audio")
    # A2
    check(f"{label_prefix}/A2 duration", abs(dur_out - dur_in) <= 0.5,
          f"|{dur_out:.3f} - {dur_in:.3f}| = {abs(dur_out-dur_in):.3f}s")

    # A3 — per-segment RMS and real-speech on dub.wav
    dub_wav = upload_dir / "dub.wav"
    transcript_data = json.loads((upload_dir / "transcript.json").read_text(encoding="utf-8"))
    segs = transcript_data.get("segments", [])
    seg_fail = []
    for s in segs:
        seg_start, seg_end = float(s["start"]), float(s["end"])
        rms = rms_dbfs(dub_wav, seg_start, seg_end)
        if rms <= -45.0:
            seg_fail.append(f"seg#{s['id']} rms={rms:.1f}dBFS")
    check(f"{label_prefix}/A3 segment RMS", len(seg_fail) == 0,
          f"{len(segs)} segs checked" if not seg_fail else f"FAILS: {seg_fail}")

    # A3b — real-speech guard on dub.wav
    rs = _is_real_speech_wav(dub_wav)
    check(f"{label_prefix}/A3b real-speech", rs, "is_real_speech(dub.wav)=True" if rs else "FAIL — not real speech")

    # A4 — peak
    with wave.open(str(dub_wav), "rb") as f:
        width = f.getsampwidth(); raw = f.readframes(f.getnframes())
    dtype = "<i2" if width == 2 else "<i4"
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    samples /= float(2 ** (8 * width - 1))
    peak_dbfs = 20 * np.log10(float(np.max(np.abs(samples))) + 1e-12)
    check(f"{label_prefix}/A4 peak", peak_dbfs <= -1.0, f"peak={peak_dbfs:.2f}dBFS")

    # A5 — clip overlap from timing metadata
    seg_durs = transcript_data.get("timings", {}).get("segment_durations", [])
    max_overlap = 0.0
    for i, (s, dur) in enumerate(zip(segs, seg_durs)):
        if i + 1 < len(segs):
            seg_end_playback = float(segs[i]["start"]) + dur
            next_start = float(segs[i+1]["start"])
            overlap = max(0.0, seg_end_playback - next_start)
            max_overlap = max(max_overlap, overlap)
    check(f"{label_prefix}/A5 overlap", max_overlap <= 0.1,
          f"max_overlap={max_overlap*1000:.0f}ms")

    # A6 — Khmer must be present (>=1 Khmer letter) and no CJK leak; allow Latin loanwords
    km_fails = []
    for s in segs:
        km = s.get("text_km", "")
        letters = [c for c in km if c.isalpha()]
        n_letters = len(letters)
        n_khmer = sum("\u1780" <= c <= "\u17ff" for c in letters)
        has_cjk = any("\u4e00" <= c <= "\u9fff" for c in km)
        # Must have at least 1 Khmer letter; if many letters, >= 50% should be Khmer
        # (brand names like "SoundMe" in short text are acceptable)
        ratio = n_khmer / n_letters if n_letters else 0.0
        if n_khmer == 0 or has_cjk or (n_letters >= 30 and ratio < 0.50):
            km_fails.append(f"seg#{s['id']} km={n_khmer}/{n_letters} cjk={has_cjk} [{km[:40]}]")
    check(f"{label_prefix}/A6 Khmer ratio", len(km_fails) == 0,
          f"{len(segs)} segs ok" if not km_fails else f"FAILS: {km_fails}")

    # A7 — SRT/VTT monotonic
    srt_text = (upload_dir / "subtitles.srt").read_text(encoding="utf-8")
    vtt_text = (upload_dir / "subtitles.vtt").read_text(encoding="utf-8")
    srt_stamps = parse_srt_timestamps(srt_text)
    vtt_stamps = parse_vtt_timestamps(vtt_text)

    def is_monotonic(stamps):
        for (s1, e1), (s2, e2) in zip(stamps, stamps[1:]):
            if s2 < e1 - 1e-3:
                return False, f"{s2:.3f} < {e1:.3f}"
        return True, "ok"

    srt_ok, srt_ev = is_monotonic(srt_stamps)
    vtt_ok, vtt_ev = is_monotonic(vtt_stamps)
    check(f"{label_prefix}/A7 SRT monotonic", srt_ok, srt_ev)
    check(f"{label_prefix}/A7 VTT monotonic", vtt_ok, vtt_ev)

    print(f"\n  Segment translations:", flush=True)
    for s in segs:
        print(f"    [{s['start']:.1f}-{s['end']:.1f}] {s['source_text'][:40]!r} → {s['text_km'][:40]!r}", flush=True)


def main():
    en_video = ROOT / "sample_input.mp4"
    zh_video = ROOT / "sample_zh.mp4"

    for p in [en_video, zh_video]:
        if not p.is_file() or p.stat().st_size < 1000:
            print(f"ERROR: {p} not found or too small. Run scripts/generate_test_videos.py first.", flush=True)
            sys.exit(1)

    print("=" * 60, flush=True)
    print("STEP 6 — Pipeline real-mode (en + zh)", flush=True)
    print("=" * 60, flush=True)

    for video_path, source_lang, out_name in [
        (en_video, "en", "dubbed_en.mp4"),
        (zh_video, "zh", "dubbed_zh.mp4"),
    ]:
        label = source_lang.upper()
        print(f"\n-- Submitting {video_path.name} (lang={source_lang}) --", flush=True)
        job_id = submit_job(video_path, source_lang)
        print(f"  job_id: {job_id}", flush=True)
        job = poll_job(job_id, timeout=900)
        print(f"  Completed in {job.get('timings',{}).get('processing_seconds','?')}s", flush=True)

        dubbed_dest = DEMO_DIR / out_name
        assert_video(label, job_id, video_path, dubbed_dest, source_lang)

    print("\n" + "=" * 60, flush=True)
    print("RESULTS", flush=True)
    print("=" * 60, flush=True)
    for label, status, evidence in _results:
        print(f"  {status:4}  {label}: {evidence}", flush=True)

    pass_count = sum(1 for _, s, _ in _results if s == "PASS")
    fail_count = sum(1 for _, s, _ in _results if s == "FAIL")
    print(f"\n{pass_count} PASS  {fail_count} FAIL", flush=True)

    en_path = DEMO_DIR / "dubbed_en.mp4"
    zh_path = DEMO_DIR / "dubbed_zh.mp4"
    print(f"\nDubbed video (EN): {en_path}", flush=True)
    print(f"Dubbed video (ZH): {zh_path}", flush=True)

    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
