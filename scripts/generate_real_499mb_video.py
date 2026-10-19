"""Generate a realistic ~495-498 MB high-bitrate 1080p MP4 video with real speech track for Phase 8 Item 5."""
from __future__ import annotations
import os, sys, subprocess, shutil, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = os.getenv("FFMPEG", "ffmpeg")
FFPROBE = os.getenv("FFPROBE", "ffprobe")

def generate_real_499mb_video() -> Path:
    out_dir = ROOT / "tests" / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_mp4 = out_dir / "bench_real_498mb_zh.mp4"

    # Target: 497 MB = 497 * 1024 * 1024 = 521,142,272 bytes
    if target_mp4.is_file():
        sz = target_mp4.stat().st_size
        sz_mb = sz / (1024 * 1024)
        if 485 <= sz_mb <= 499.5:
            print(f"  Reusing existing {target_mp4} ({sz_mb:.2f} MB)")
            return target_mp4

    print("  Synthesizing real Chinese speech audio track...")
    import asyncio, edge_tts
    speech_mp3 = out_dir / "temp_zh_speech.mp3"
    speech_wav = out_dir / "temp_zh_speech.wav"
    
    sentences = [
        "欢迎使用Soundme高保真柬埔寨语配音工作室。",
        "本次基准测试验证大容量视频处理能力。",
        "自然语言处理与多语言语音克隆技术正迅速发展。",
        "湄公河流经东南亚多个国家，孕育了丰富的文化。",
        "金边是充满活力的商业与文化中心。",
        "现代多媒体处理管线必须具备高吞吐量与低延迟。",
        "柬埔寨的高棉文化拥有悠久而璀璨的历史遗产。",
        "人工智能正在深度赋能音视频本地化行业。"
    ]
    full_text = " ".join(sentences * 2)

    async def gen_speech():
        c = edge_tts.Communicate(full_text, "zh-CN-XiaoxiaoNeural")
        await c.save(str(speech_mp3))
    asyncio.run(gen_speech())

    subprocess.run([
        FFMPEG, "-y", "-i", str(speech_mp3),
        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(speech_wav)
    ], check=True, capture_output=True)
    speech_mp3.unlink(missing_ok=True)

    # Probe speech duration
    probe = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-show_format", "-of", "json", str(speech_wav)],
        capture_output=True, text=True, check=True
    ).stdout)
    dur = float(probe["format"]["duration"])
    print(f"  Speech audio duration: {dur:.1f}s")

    # Target ~496 MB across dur seconds
    target_bytes = 496 * 1024 * 1024
    bitrate_kbps = int((target_bytes * 8) / (dur * 1000) * 0.98)
    print(f"  Encoding 1080p video with noise at {bitrate_kbps} kbps for {dur:.1f}s...")

    cmd = [
        FFMPEG, "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30,noise=alls=20:allf=t+u",
        "-i", str(speech_wav),
        "-t", f"{dur:.2f}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-b:v", f"{bitrate_kbps}k",
        "-minrate", f"{bitrate_kbps}k",
        "-maxrate", f"{bitrate_kbps}k",
        "-bufsize", f"{int(bitrate_kbps * 0.5)}k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(target_mp4)
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    speech_wav.unlink(missing_ok=True)

    final_size = target_mp4.stat().st_size
    final_mb = final_size / (1024 * 1024)
    print(f"  Generated real video: {target_mp4} ({final_mb:.2f} MB, {final_size} bytes)")
    return target_mp4

if __name__ == "__main__":
    generate_real_499mb_video()
