from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import sys
import time
from pathlib import Path

import edge_tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def main() -> int:
    print(f"edge-tts={importlib.metadata.version('edge-tts')}")
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "demo/edge-live-test.mp3")
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        await edge_tts.Communicate("សួស្តី។ នេះជាការសាកល្បងសំឡេង។", voice="km-KH-SreymomNeural").save(str(destination))
        print(f"EDGE_RESULT=PASS bytes={destination.stat().st_size} elapsed_s={time.monotonic()-started:.2f} artifact={destination}")
        return 0
    except Exception as exc:
        destination.unlink(missing_ok=True)
        print(f"EDGE_RESULT=FAIL exception={type(exc).__name__} detail={str(exc)[:600]} elapsed_s={time.monotonic()-started:.2f}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
