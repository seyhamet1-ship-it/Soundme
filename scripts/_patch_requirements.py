from pathlib import Path

path = Path(__file__).resolve().parents[1] / "requirements.txt"
raw = path.read_bytes()
old = b"faster-whisper==1.0.3\nedge-tts==7.3.1"
new = b"faster-whisper==1.2.1\nav==19.0.1\nedge-tts==7.3.1"
assert old in raw, "pattern not found"
path.write_bytes(raw.replace(old, new, 1))
print("requirements.txt updated")
