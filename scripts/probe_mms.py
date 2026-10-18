from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import torch
from transformers import AutoTokenizer, VitsModel
from app.audio_quality import require_real_speech

MODEL = "facebook/mms-tts-khm"
TEXT = "សួស្តី។ នេះជាការសាកល្បងសំឡេង។"
DESTINATION = Path("demo/mms-live-test.wav")


def main() -> int:
    started = time.monotonic()
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    try:
        tokenizer = AutoTokenizer.from_pretrained(MODEL)
        model = VitsModel.from_pretrained(MODEL).eval()
        encoded = tokenizer(TEXT, return_tensors="pt")
        with torch.inference_mode():
            samples = model(**encoded).waveform.squeeze().cpu().numpy()
        normalized = np.clip(samples / max(1.0, float(np.max(np.abs(samples))) / 0.89), -0.89, 0.89)
        pcm = (normalized * 32767).astype("<i2")
        with wave.open(str(DESTINATION), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(model.config.sampling_rate)
            output.writeframes(pcm.tobytes())
        require_real_speech(DESTINATION)
        print(f"MMS_RESULT=PASS model={MODEL} seconds={len(samples)/model.config.sampling_rate:.2f} sample_rate={model.config.sampling_rate} peak={float(np.max(np.abs(normalized))):.4f} speech_guard=PASS elapsed_s={time.monotonic()-started:.2f} artifact={DESTINATION}")
        return 0
    except Exception as exc:
        DESTINATION.unlink(missing_ok=True)
        print(f"MMS_RESULT=FAIL exception={type(exc).__name__} detail={str(exc)[:800]} elapsed_s={time.monotonic()-started:.2f}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
