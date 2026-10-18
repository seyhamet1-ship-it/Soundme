"""Step 2 — REPRODUCE: GUI request replay, text logging, and tracebacks for NoAudioReceived and MMS narrow()."""
from __future__ import annotations
import json, subprocess, sys, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch
from transformers import AutoTokenizer, VitsModel
import edge_tts

def log_text(label: str, text: str):
    print(f"[{label}] repr(text): {repr(text)}")
    hex_pts = " ".join(f"U+{ord(c):04X}" for c in text)
    print(f"[{label}] hex: {hex_pts}")

def reproduce_preview_curl():
    print("=== REPRODUCING GUI PREVIEW REQUEST WITH CURL ===")
    cmd = [
        "curl.exe", "-s", "-i", "-X", "POST", "http://127.0.0.1:8000/api/tts/preview",
        "-H", "Content-Type: application/json",
        "-d", '{"voice":"km-KH-SreymomNeural"}'
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(res.stdout[:500])

def reproduce_no_audio_received():
    print("\n=== REPRODUCING Edge TTS NoAudioReceived ===")
    failing_text = "..."
    log_text("EdgeTTS input", failing_text)
    try:
        import asyncio
        async def _call():
            await edge_tts.Communicate(failing_text, voice="km-KH-SreymomNeural").save("demo/test_noaudio.mp3")
        asyncio.run(_call())
    except Exception as exc:
        traceback.print_exc(file=sys.stdout)

def reproduce_mms_narrow():
    print("\n=== REPRODUCING MMS narrow() ERROR ===")
    failing_text = "..."
    log_text("MMS input", failing_text)
    try:
        tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-khm")
        model = VitsModel.from_pretrained("facebook/mms-tts-khm").eval()
        encoded = tokenizer(failing_text, return_tensors="pt")
        print(f"Tokenized input_ids: {encoded.input_ids} (shape {encoded.input_ids.shape})")
        with torch.inference_mode():
            _ = model(**encoded)
    except Exception as exc:
        traceback.print_exc(file=sys.stdout)

if __name__ == "__main__":
    reproduce_preview_curl()
    reproduce_no_audio_received()
    reproduce_mms_narrow()
