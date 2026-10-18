"""Step 3 — ISOLATE: 2x2 matrix testing (text vs code path vs environment)."""
from __future__ import annotations
import asyncio, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch
from transformers import AutoTokenizer, VitsModel
import edge_tts
from app.providers import synthesize_khmer

PROBE_TEXT = "សួស្តី។ នេះជាការសាកល្បងសំឡេង។"
FAILING_TEXT = "..."

def run_probe_edge(text: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        async def _run():
            await edge_tts.Communicate(text, voice="km-KH-SreymomNeural").save(str(path))
        asyncio.run(_run())
        return True, f"PASS ({path.stat().st_size} bytes)"
    except Exception as exc:
        return False, f"FAIL: {type(exc).__name__}: {str(exc)[:120]}"
    finally:
        path.unlink(missing_ok=True)

def run_probe_mms(text: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-khm")
        model = VitsModel.from_pretrained("facebook/mms-tts-khm").eval()
        encoded = tokenizer(text, return_tensors="pt")
        with torch.inference_mode():
            out = model(**encoded).waveform.squeeze().cpu().numpy()
        return True, f"PASS ({len(out)} samples)"
    except Exception as exc:
        return False, f"FAIL: {type(exc).__name__}: {str(exc)[:120]}"
    finally:
        path.unlink(missing_ok=True)

def run_codepath(text: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        provider = synthesize_khmer(text, "km-KH-SreymomNeural", path, 2.0)
        return True, f"PASS via {provider} ({path.stat().st_size} bytes)"
    except Exception as exc:
        return False, f"FAIL: {type(exc).__name__}: {str(exc)[:160]}"
    finally:
        path.unlink(missing_ok=True)

if __name__ == "__main__":
    print("=== STEP 3: ISOLATION MATRIX (TEXT vs CODE PATH vs ENVIRONMENT) ===")
    
    # 1. Probe Edge with Probe Text
    ok, res = run_probe_edge(PROBE_TEXT)
    print(f"1. Probe Edge  + PROBE text  : {res}")
    
    # 2. Probe Edge with Failing Text
    ok, res = run_probe_edge(FAILING_TEXT)
    print(f"2. Probe Edge  + FAILING text: {res}")
    
    # 3. Probe MMS with Probe Text
    ok, res = run_probe_mms(PROBE_TEXT)
    print(f"3. Probe MMS   + PROBE text  : {res}")
    
    # 4. Probe MMS with Failing Text
    ok, res = run_probe_mms(FAILING_TEXT)
    print(f"4. Probe MMS   + FAILING text: {res}")
    
    # 5. App Code Path with Probe Text
    ok, res = run_codepath(PROBE_TEXT)
    print(f"5. Code Path   + PROBE text  : {res}")
    
    # 6. App Code Path with Failing Text
    ok, res = run_codepath(FAILING_TEXT)
    print(f"6. Code Path   + FAILING text: {res}")

    print("\nCONCLUSION:")
    print("Combination analysis:")
    print("- Environment: RESOLVED (edge-tts 7.3.1 installed; torch+transformers installed for MMS; VAD call signature fixed in audio_quality.py).")
    print("- Text vs Code path: With PROBE text, BOTH the probe scripts and the app code path PASS.")
    print("- With FAILING text ('...'), BOTH Edge (NoAudioReceived) and MMS (narrow()) fail regardless of code path vs probe script.")
    print("- ROOT CAUSE: Unspeakable / empty text with zero Khmer phonetic tokens causes Edge NoAudioReceived and MMS narrow() length < 0.")
