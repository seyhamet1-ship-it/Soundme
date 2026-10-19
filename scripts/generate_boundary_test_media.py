"""Generate boundary test media for Phase 7B:
1. test_499mb.mp4: ~499 MiB (523,239,424 bytes), 5-second valid MP4 with speech audio.
2. test_501mb.mp4: ~501 MiB (525,336,576 bytes), exceeds 500 MB limit by 1 MB.
3. test_31min.mp4: 31-minute valid low-bitrate MP4 (~1.5 MB), exceeds 30-min duration limit.
"""
from __future__ import annotations
import os, sys, struct, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = ROOT / "tests" / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
FFMPEG = os.getenv("FFMPEG", "ffmpeg")

def generate_base_5s_clip(dest: Path) -> None:
    """Generate a clean 5-second MP4 with 1 video stream and 1 speech/audio stream."""
    cmd = [
        FFMPEG, "-y",
        "-f", "lavfi", "-i", "color=c=0x1a1a2e:s=320x240:d=5:r=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35",
        "-c:a", "aac", "-b:a", "64k",
        "-shortest", str(dest)
    ]
    subprocess.run(cmd, check=True, capture_output=True)

def pad_mp4_with_free_atom(src_mp4: Path, dest_mp4: Path, target_bytes: int) -> None:
    """Insert a standard ISOBMFF 'free' atom so the file reaches target_bytes."""
    src_data = src_mp4.read_bytes()
    src_len = len(src_data)
    if src_len >= target_bytes:
        raise ValueError(f"Source size ({src_len}) already >= target ({target_bytes})")
    
    # In MP4/ISOBMFF, a 'free' atom structure is:
    # 4 bytes size (big-endian), 4 bytes type b'free', followed by (size - 8) payload bytes.
    pad_total = target_bytes - src_len
    if pad_total < 8:
        raise ValueError("Padding must be at least 8 bytes for atom header")
    
    atom_header = struct.pack(">I", pad_total) + b"free"
    chunk_size = 1024 * 1024  # 1 MB chunk for fast writing
    remaining = pad_total - 8
    
    with open(dest_mp4, "wb") as f:
        f.write(src_data)
        f.write(atom_header)
        zero_buf = b"\x00" * chunk_size
        while remaining > 0:
            to_write = min(remaining, chunk_size)
            f.write(zero_buf[:to_write])
            remaining -= to_write

def generate_31min_clip(dest: Path) -> None:
    """Generate a 31-minute (1860s) ultra-low-bitrate MP4 (~1.5 MB)."""
    cmd = [
        FFMPEG, "-y",
        "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1860:r=1",
        "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
        "-t", "1860",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "51",
        "-c:a", "aac", "-b:a", "8k",
        "-shortest", str(dest)
    ]
    subprocess.run(cmd, check=True, capture_output=True)

def main():
    print("Generating boundary test media...")
    base_5s = MEDIA_DIR / "base_5s_temp.mp4"
    if not base_5s.exists():
        print("  1. Creating 5s base clip...")
        generate_base_5s_clip(base_5s)
    
    # 499 MiB: 499 * 1024 * 1024 = 523,239,424 bytes
    target_499mb = 499 * 1024 * 1024
    file_499mb = MEDIA_DIR / "test_499mb.mp4"
    if not file_499mb.exists() or file_499mb.stat().st_size != target_499mb:
        print(f"  2. Generating test_499mb.mp4 ({target_499mb} bytes)...")
        pad_mp4_with_free_atom(base_5s, file_499mb, target_499mb)
        print(f"     test_499mb.mp4 ready: {file_499mb.stat().st_size} bytes")
    else:
        print(f"  2. Reusing test_499mb.mp4 ({file_499mb.stat().st_size} bytes)")

    # 501 MiB: 501 * 1024 * 1024 = 525,336,576 bytes
    target_501mb = 501 * 1024 * 1024
    file_501mb = MEDIA_DIR / "test_501mb.mp4"
    if not file_501mb.exists() or file_501mb.stat().st_size != target_501mb:
        print(f"  3. Generating test_501mb.mp4 ({target_501mb} bytes)...")
        pad_mp4_with_free_atom(base_5s, file_501mb, target_501mb)
        print(f"     test_501mb.mp4 ready: {file_501mb.stat().st_size} bytes")
    else:
        print(f"  3. Reusing test_501mb.mp4 ({file_501mb.stat().st_size} bytes)")

    # 31 minutes: 1860 seconds
    file_31min = MEDIA_DIR / "test_31min.mp4"
    if not file_31min.exists():
        print("  4. Generating test_31min.mp4 (1860s)...")
        generate_31min_clip(file_31min)
        print(f"     test_31min.mp4 ready: {file_31min.stat().st_size / 1024 / 1024:.2f} MB")
    else:
        print(f"  4. Reusing test_31min.mp4 ({file_31min.stat().st_size / 1024 / 1024:.2f} MB)")

    base_5s.unlink(missing_ok=True)
    print("All boundary test media generated successfully!")

if __name__ == "__main__":
    main()
