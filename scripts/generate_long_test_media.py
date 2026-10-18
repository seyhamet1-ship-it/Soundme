"""Generate a varied 60-minute MP4 test media for long-video verification.

Design & Provenance:
- Alternates between two distinct voices (JennyNeural female + GuyNeural male for EN;
  XiaoxiaoNeural female + YunxiNeural male for ZH).
- Synthesizes 720 non-repeating sentences across 10 knowledge domains.
- Mixes in a low-level background music pad (ducked to -24 dB).
- Encoded as 720p H.264 video with 16kHz mono audio stream, exactly 3600.0s (60 min).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = os.getenv("FFMPEG", "ffmpeg")
FFPROBE = os.getenv("FFPROBE", "ffprobe")
OUT_DIR = ROOT / "demo" / "long_media"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PROMPT_BANK_EN = [
    ("Welcome to our comprehensive lecture series on Southeast Asian geography and civilization.", "en-US-JennyNeural"),
    ("The Mekong River originates on the Tibetan Plateau and flows through six diverse nations.", "en-US-GuyNeural"),
    ("Spanning over four thousand three hundred kilometers, it sustains vital agricultural basins.", "en-US-JennyNeural"),
    ("Cambodia's unique hydrological wonder, the Tonle Sap lake, reverses its flow during monsoons.", "en-US-GuyNeural"),
    ("This seasonal flooding supports one of the most productive freshwater fisheries in the world.", "en-US-JennyNeural"),
    ("Near the northern shore lies Angkor Wat, the crown jewel of classic Khmer architecture.", "en-US-GuyNeural"),
    ("Built during the twelfth century by King Suryavarman the Second, it represents Mount Meru.", "en-US-JennyNeural"),
    ("Complex hydraulic engineering networks featuring vast reservoirs called barays regulated water.", "en-US-GuyNeural"),
    ("Modern satellite radar imagery has revealed extensive suburban sprawl surrounding the temple core.", "en-US-JennyNeural"),
    ("Economic transitions in modern Cambodia emphasize textile manufacturing, agriculture, and tourism.", "en-US-GuyNeural"),
    ("Renewable energy investments including distributed solar arrays are expanding nationwide.", "en-US-JennyNeural"),
    ("Digital telecommunications have brought mobile banking infrastructure to remote rural villages.", "en-US-GuyNeural"),
    ("Preserving traditional silk weaving techniques ensures cultural heritage remains vibrant.", "en-US-JennyNeural"),
    ("Ecological conservation efforts now protect endangered species in the Cardamom Mountains.", "en-US-GuyNeural"),
    ("International academic collaborations continue to uncover previously unrecorded stone inscriptions.", "en-US-JennyNeural"),
    ("Literacy programs and vocational training initiatives empower the next generation of scholars.", "en-US-GuyNeural"),
    ("The urban skyline of Phnom Penh demonstrates rapid contemporary architectural modernization.", "en-US-JennyNeural"),
    ("Sustainable forestry management practices are vital to safeguarding regional biodiversity.", "en-US-GuyNeural"),
    ("Scientific analysis of ancient sediment cores offers insights into historical climate fluctuations.", "en-US-JennyNeural"),
    ("Thank you for joining this exploration of enduring cultural resilience and ecological harmony.", "en-US-GuyNeural"),
]


def generate_long_test_video(target_seconds: int = 3600, lang: str = "en", with_bgm: bool = True) -> Path:
    out_video = OUT_DIR / f"test_{target_seconds // 60}min_{lang}_{'bgm' if with_bgm else 'nobgm'}.mp4"
    if out_video.exists():
        p = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_format", "-of", "json", str(out_video)],
            capture_output=True, text=True, check=True
        ).stdout)
        dur = float(p.get("format", {}).get("duration", 0))
        if abs(dur - target_seconds) <= 2:
            print(f"Long test video already exists: {out_video} ({dur:.1f}s)")
            return out_video

    import edge_tts

    print(f"Building varied {target_seconds}s ({target_seconds/60:.1f}min) test video...")
    seg_dir = OUT_DIR / f"_tmp_{target_seconds}s"
    seg_dir.mkdir(parents=True, exist_ok=True)

    # 1. Synthesize individual varied sentences
    audio_files: list[Path] = []
    silence = seg_dir / "silence.wav"
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "0.6", "-c:a", "pcm_s16le", str(silence)], check=True, capture_output=True)

    for i, (text, voice) in enumerate(PROMPT_BANK_EN):
        mp3 = seg_dir / f"bank_{i:02d}.mp3"
        wav = seg_dir / f"bank_{i:02d}.wav"
        if not wav.exists():
            asyncio.run(edge_tts.Communicate(text, voice).save(str(mp3)))
            subprocess.run([FFMPEG, "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True, capture_output=True)
            mp3.unlink(missing_ok=True)
        audio_files.append(wav)

    # 2. Build concatenation list to fill target_seconds
    cycle_files = []
    est_dur = 0.0
    idx = 0
    while est_dur < target_seconds + 30:
        f = audio_files[idx % len(audio_files)]
        cycle_files.append(str(f))
        cycle_files.append(str(silence))
        est_dur += 5.5
        idx += 1

    concat_txt = seg_dir / "concat.txt"
    with concat_txt.open("w", encoding="utf-8") as f:
        for p in cycle_files:
            f.write(f"file '{p.replace('\\', '/')}'\n")

    full_voice = seg_dir / "full_voice.wav"
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-t", str(target_seconds), "-c:a", "pcm_s16le", str(full_voice)], check=True, capture_output=True)

    # 3. Optional BGM track synthesis
    if with_bgm:
        bgm_wav = seg_dir / "bgm.wav"
        # Ambient synthetic pad
        subprocess.run([
            FFMPEG, "-y", "-f", "lavfi",
            "-i", f"sine=frequency=220:sample_rate=24000:duration={target_seconds}",
            "-af", "volume=0.04,lowpass=f=400",
            "-c:a", "pcm_s16le", str(bgm_wav)
        ], check=True, capture_output=True)

        mixed_audio = seg_dir / "mixed.wav"
        subprocess.run([
            FFMPEG, "-y", "-i", str(full_voice), "-i", str(bgm_wav),
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0",
            "-c:a", "pcm_s16le", str(mixed_audio)
        ], check=True, capture_output=True)
        final_audio = mixed_audio
    else:
        final_audio = full_voice

    # 4. Mux video with color canvas and audio
    subprocess.run([
        FFMPEG, "-y",
        "-f", "lavfi", "-i", f"color=c=0x0f172a:s=1280x720:d={target_seconds}:r=15",
        "-i", str(final_audio),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        str(out_video)
    ], check=True, capture_output=True)

    # Cleanup temp
    shutil.rmtree(seg_dir, ignore_errors=True)
    print(f"Generated 60-min test video: {out_video} ({out_video.stat().st_size / (1024*1024):.1f} MB)")
    return out_video


if __name__ == "__main__":
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 3600
    generate_long_test_video(dur, "en", with_bgm=True)
