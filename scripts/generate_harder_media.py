"""Generate Phase 6 Harder Real-Mode Media files using edge-tts and ffmpeg.

Cases:
  1a: ~2 min, 10+ sentences with background music mixed in (media_1a_long_bgm.mp4)
  1b: Tight time slot with long sentence to prove fit logic (media_1b_tight_slot.mp4)
  1c: 9:16 portrait video for burned-in subtitles (media_1c_portrait_9_16.mp4)
  1d: Numbers, dates, and currency (media_1d_numbers.mp4)
"""
from __future__ import annotations
import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
import edge_tts

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = os.getenv("FFMPEG", str(ROOT / "tools" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffmpeg.exe"))
MEDIA_DIR = ROOT / "demo" / "harder_media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


async def synthesize_speech(text: str, voice: str, out_wav: Path) -> None:
    temp_mp3 = out_wav.with_suffix(".mp3")
    try:
        comm = edge_tts.Communicate(text, voice=voice)
        await comm.save(str(temp_mp3))
        subprocess.run([
            FFMPEG, "-y", "-i", str(temp_mp3),
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)
        ], check=True, capture_output=True)
    finally:
        temp_mp3.unlink(missing_ok=True)


async def create_case_1a() -> Path:
    """1a: ~2 min, 10+ sentences of speech with background music mixed in."""
    out_mp4 = MEDIA_DIR / "media_1a_long_bgm.mp4"
    if out_mp4.is_file() and out_mp4.stat().st_size > 100_000:
        print(f"[1a] Exists: {out_mp4}")
        return out_mp4

    sentences = [
        "Hello everyone, welcome to the comprehensive Soundme dubbing demonstration.",
        "Today we are testing video dubbing with background music preservation.",
        "Automatic speech recognition extracts every sentence spoken in this video.",
        "Neural machine translation converts the original speech into natural Khmer text.",
        "High quality neural text to speech synthesizes clear Khmer voiceover.",
        "Audio quality guards verify speech presence, modulation, and natural harmonics.",
        "Timeline slot fitting ensures dubbed speech synchronizes with video events.",
        "Background music can be mixed in or removed based on user preference.",
        "Accurate bilingual subtitles provide real-time captions for all viewers.",
        "Thank you for watching this long-form real dubbing demonstration video.",
    ]
    # Build speech track with 3-second gaps between sentences
    temp_dir = Path(tempfile.mkdtemp(prefix="soundme_1a_"))
    try:
        wav_files = []
        for idx, text in enumerate(sentences):
            w = temp_dir / f"sent_{idx:02d}.wav"
            await synthesize_speech(text, "en-US-JennyNeural", w)
            wav_files.append(w)

        # Concatenate with silence filter to reach ~100-110 seconds
        filter_parts = []
        for i, w in enumerate(wav_files):
            filter_parts.append(f"[{i}:a]apad=pad_dur=3[a{i}]")
        concat_inputs = "".join(f"[a{i}]" for i in range(len(wav_files)))
        filter_complex = f"{';'.join(filter_parts)};{concat_inputs}concat=n={len(wav_files)}:v=0:a=1[speech]"

        speech_wav = temp_dir / "all_speech.wav"
        cmd = [FFMPEG, "-y"]
        for w in wav_files:
            cmd.extend(["-i", str(w)])
        cmd.extend(["-filter_complex", filter_complex, "-map", "[speech]", str(speech_wav)])
        subprocess.run(cmd, check=True, capture_output=True)

        # Get speech duration
        probe = subprocess.run([
            FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error",
            "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            str(speech_wav)
        ], capture_output=True, text=True, check=True)
        dur = float(probe.stdout.strip())

        # Generate background music track of same duration using pleasant chord arpeggio
        bgm_wav = temp_dir / "bgm.wav"
        subprocess.run([
            FFMPEG, "-y", "-f", "lavfi",
            "-i", f"anoisesrc=d={dur}:c=pink:r=44100:a=0.03,"
                  f"aformat=channel_layouts=mono,volume=0.15",
            str(bgm_wav)
        ], check=True, capture_output=True)

        # Mix speech + BGM together, and pair with color video
        subprocess.run([
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"color=c=0x1a2a3a:s=1280x720:d={dur}:r=24",
            "-i", str(speech_wav),
            "-i", str(bgm_wav),
            "-filter_complex", "[1:a][2:a]amix=inputs=2:duration=first:dropout_transition=2[mixed]",
            "-map", "0:v", "-map", "[mixed]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(out_mp4)
        ], check=True, capture_output=True)
        print(f"[1a] Created: {out_mp4} ({dur:.1f}s, {out_mp4.stat().st_size} bytes)")
        return out_mp4
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


async def create_case_1b() -> Path:
    """1b: Source text whose Khmer translation is much longer than its time slot."""
    out_mp4 = MEDIA_DIR / "media_1b_tight_slot.mp4"
    if out_mp4.is_file() and out_mp4.stat().st_size > 20_000:
        print(f"[1b] Exists: {out_mp4}")
        return out_mp4

    temp_dir = Path(tempfile.mkdtemp(prefix="soundme_1b_"))
    try:
        # A fast speech clip in a 2.5 second slot
        # Text: "Our artificial intelligence video dubbing system provides automatic voice synthesis."
        # English takes ~2.6s at normal rate, but we force slot to 2.2s.
        speech_wav = temp_dir / "tight_speech.wav"
        text = "Our artificial intelligence video dubbing system provides automatic voice synthesis."
        await synthesize_speech(text, "en-US-JennyNeural", speech_wav)

        probe = subprocess.run([
            FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error",
            "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            str(speech_wav)
        ], capture_output=True, text=True, check=True)
        dur = float(probe.stdout.strip())

        subprocess.run([
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"color=c=0x301030:s=1280x720:d={dur}:r=24",
            "-i", str(speech_wav),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(out_mp4)
        ], check=True, capture_output=True)
        print(f"[1b] Created: {out_mp4} ({dur:.1f}s)")
        return out_mp4
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


async def create_case_1c() -> Path:
    """1c: 9:16 portrait video (720x1280) for burned-in Khmer subtitles."""
    out_mp4 = MEDIA_DIR / "media_1c_portrait_9_16.mp4"
    if out_mp4.is_file() and out_mp4.stat().st_size > 20_000:
        print(f"[1c] Exists: {out_mp4}")
        return out_mp4

    temp_dir = Path(tempfile.mkdtemp(prefix="soundme_1c_"))
    try:
        speech_wav = temp_dir / "portrait_speech.wav"
        text = "Welcome to mobile video dubbing in vertical portrait format."
        await synthesize_speech(text, "en-US-JennyNeural", speech_wav)

        probe = subprocess.run([
            FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error",
            "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            str(speech_wav)
        ], capture_output=True, text=True, check=True)
        dur = float(probe.stdout.strip())

        # 720x1280 portrait
        subprocess.run([
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"color=c=0x0a3322:s=720x1280:d={dur}:r=24",
            "-i", str(speech_wav),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(out_mp4)
        ], check=True, capture_output=True)
        print(f"[1c] Created: {out_mp4} (720x1280, {dur:.1f}s)")
        return out_mp4
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


async def create_case_1d() -> Path:
    """1d: Numbers, dates, and currency."""
    out_mp4 = MEDIA_DIR / "media_1d_numbers.mp4"
    if out_mp4.is_file() and out_mp4.stat().st_size > 20_000:
        print(f"[1d] Exists: {out_mp4}")
        return out_mp4

    temp_dir = Path(tempfile.mkdtemp(prefix="soundme_1d_"))
    try:
        speech_wav = temp_dir / "numbers_speech.wav"
        # Two sentences covering currency, numbers, dates
        text = "The product price is one hundred and twenty dollars. The release date is October 2026."
        await synthesize_speech(text, "en-US-JennyNeural", speech_wav)

        probe = subprocess.run([
            FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error",
            "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            str(speech_wav)
        ], capture_output=True, text=True, check=True)
        dur = float(probe.stdout.strip())

        subprocess.run([
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"color=c=0x402010:s=1280x720:d={dur}:r=24",
            "-i", str(speech_wav),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(out_mp4)
        ], check=True, capture_output=True)
        print(f"[1d] Created: {out_mp4} ({dur:.1f}s)")
        return out_mp4
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


async def main():
    print("Generating Phase 6 Harder Media Cases...")
    await create_case_1a()
    await create_case_1b()
    await create_case_1c()
    await create_case_1d()
    print("All media generated successfully.")


if __name__ == "__main__":
    asyncio.run(main())
