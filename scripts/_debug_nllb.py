import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers import translate_khmer
from app.segments import is_khmer_text

TEXT = "Hello, welcome to Soundme. This is a short English test sentence for dubbing."
translated, provider = translate_khmer(TEXT, "en")
print(f"provider={provider}")
print(f"khmer={is_khmer_text(translated)}")
print(f"text={translated}")
