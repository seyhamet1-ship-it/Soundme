"""Step 1 — PROBES: inspect demo/edge-live-test.mp3 and demo/mms-live-test.wav."""
from __future__ import annotations
import json, subprocess, sys, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from app.audio_quality import is_real_speech

FFPROBE = str(ROOT / "tools" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffprobe.exe")
FFMPEG = str(ROOT / "tools" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffmpeg.exe")

def ffprobe_info(path: Path) -> dict:
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return json.loads(r.stdout)

def to_pcm16_samples(path: Path) -> tuple[np.ndarray, int]:
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            [FFMPEG, "-y", "-i", str(path), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp_path)],
            capture_output=True, check=True,
        )
        with wave.open(str(tmp_path), "rb") as f:
            raw = f.readframes(f.getnframes())
            rate = f.getframerate()
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        return samples, rate
    finally:
        tmp_path.unlink(missing_ok=True)

def guard_breakdown(samples: np.ndarray, rate: int) -> dict:
    signal = np.asarray(samples, dtype=np.float32).reshape(-1)
    signal = signal - float(np.mean(signal))
    rms = float(np.sqrt(np.mean(signal * signal)))
    peak = float(np.max(np.abs(signal)))
    
    from faster_whisper.vad import get_vad_model
    model = get_vad_model()
    state, context = model.get_initial_states(batch_size=1)
    probs = []
    for offset in range(0, signal.size, 512):
        chunk = signal[offset : offset + 512]
        if len(chunk) < 512:
            chunk = np.pad(chunk, (0, 512 - len(chunk)))
        prob, state, context = model(chunk, state, context, 16000)
        probs.append(float(np.squeeze(prob)))
    vad_probs = np.asarray(probs, dtype=np.float32)
    vad_ratio = float(np.mean(vad_probs > 0.5))

    frame = min(4096, 1 << (signal.size.bit_length() - 1))
    window = np.hanning(frame).astype(np.float32)
    powers = []
    for offset in range(0, signal.size - frame + 1, max(1, frame // 2)):
        spectrum = np.fft.rfft(signal[offset : offset + frame] * window)
        powers.append(np.square(np.abs(spectrum)))
    spectral_power = np.mean(powers, axis=0)
    total_power = float(np.sum(spectral_power))
    spectral_ratio = float(np.max(spectral_power) / total_power) if total_power > 0 else 1.0

    target_rate = 16000
    envelope_frame = max(1, round(target_rate * 0.01))
    usable = signal.size // envelope_frame * envelope_frame
    envelope = np.sqrt(np.mean(np.square(signal[:usable].reshape(-1, envelope_frame)), axis=1))
    envelope -= float(np.mean(envelope))
    modulation = np.square(np.abs(np.fft.rfft(envelope * np.hanning(envelope.size))))
    frequencies = np.fft.rfftfreq(envelope.size, d=envelope_frame / target_rate)
    band = (frequencies >= 2.0) & (frequencies <= 8.0)
    modulation_total = float(np.sum(modulation[1:]))
    mod_ratio = float(np.sum(modulation[band]) / modulation_total) if modulation_total > 0 else 0.0

    passed = is_real_speech(samples, rate)
    return {
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "vad_ratio_gt_0_5": round(vad_ratio, 4),
        "vad_threshold": "> 0.50",
        "vad_pass": vad_ratio > 0.5,
        "spectral_max_power_ratio": round(spectral_ratio, 4),
        "spectral_threshold": "< 0.50",
        "spectral_pass": spectral_ratio < 0.5,
        "syllabic_modulation_ratio": round(mod_ratio, 4),
        "syllabic_threshold": ">= 0.15",
        "syllabic_pass": mod_ratio >= 0.15,
        "is_real_speech": passed,
    }

def print_probe(file_path: str, source_text: str):
    p = ROOT / file_path
    print(f"=== {file_path} ===")
    print(f"File exists: {p.is_file()}, Size: {p.stat().st_size} bytes")
    print(f"Text repr: {repr(source_text)}")
    hex_pts = " ".join(f"U+{ord(c):04X}" for c in source_text)
    print(f"Text hex : {hex_pts}")
    
    info = ffprobe_info(p)
    stream = info["streams"][0]
    duration = info["format"]["duration"]
    codec = stream["codec_name"]
    sample_rate = stream["sample_rate"]
    print(f"ffprobe duration: {duration} s")
    print(f"ffprobe codec   : {codec}")
    print(f"ffprobe rate    : {sample_rate} Hz")

    samples, rate = to_pcm16_samples(p)
    breakdown = guard_breakdown(samples, rate)
    print("Speech Guard Numbers:")
    for k, v in breakdown.items():
        print(f"  {k}: {v}")
    if breakdown["is_real_speech"]:
        print("Verdict: REAL SPEECH (PASS)\n")
    else:
        print("Verdict: NOT REAL SPEECH (FAIL)\n")

if __name__ == "__main__":
    edge_text = "សួស្តី។ នេះជាការសាកល្បងសំឡេង។"
    mms_text = "សួស្តី។ នេះជាការសាកល្បងសំឡេង។"
    print_probe("demo/edge-live-test.mp3", edge_text)
    print_probe("demo/mms-live-test.wav", mms_text)
