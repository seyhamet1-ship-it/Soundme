import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.segments import is_khmer_text

SAMPLES = [
    "អរគុណអ្នក ដែលបានមកលេង SoundMe។",
    "នេះ ជា សម្តី សាកល្បង ភាសា អង់គ្លេស ខ្លី សម្រាប់ ការ ចម្រ២ង។",
]
for text in SAMPLES:
    letters = [c for c in text if c.isalpha()]
    khmer = sum("\u1780" <= c <= "\u17ff" for c in letters)
    non_khmer = [c for c in letters if not ("\u1780" <= c <= "\u17ff")]
    print(f"text={text[:60]}... letters={len(letters)} khmer={khmer} ratio={khmer/max(1,len(letters)):.3f} non_khmer={non_khmer[:10]} is_khmer={is_khmer_text(text)}")

