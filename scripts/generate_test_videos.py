"""Generate real test videos for en and zh using edge-tts speech and ffmpeg."""
from __future__ import annotations
import asyncio, os, subprocess
from pathlib import Path
import edge_tts

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = str(ROOT / "tools" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffmpeg.exe")

async def generate_video(text: str, voice: str, out_path: Path, color: str = "blue", duration: int = 6):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_mp3 = out_path.with_suffix(".mp3")
    try:
        t = edge_tts.Communicate(text, voice=voice)
        await t.save(str(temp_mp3))
        subprocess.run([
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=1280x720:d={duration}",
            "-i", str(temp_mp3),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "24000",
            "-shortest", str(out_path)
        ], check=True, capture_output=True)
        print(f"Created {out_path}: {out_path.stat().st_size} bytes")
    finally:
        temp_mp3.unlink(missing_ok=True)

async def main():
    en_path = ROOT / "sample_input.mp4"
    if not en_path.is_file() or en_path.stat().st_size == 0:
        await generate_video(
            "Hello, welcome to Soundme. This is a short English test sentence for dubbing.",
            "en-US-JennyNeural",
            en_path,
            color="navy",
            duration=6,
        )
    zh_path = ROOT / "sample_zh.mp4"
    if not zh_path.is_file() or zh_path.stat().st_size == 0:
        await generate_video(
            "大家好，欢迎使用 Soundme。这是一个用于配音测试的中文短视频。",
            "zh-CN-XiaoxiaoNeural",
            zh_path,
            color="darkred",
            duration=6,
        )

if __name__ == "__main__":
    asyncio.run(main())
