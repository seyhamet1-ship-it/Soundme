import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transformers import AutoTokenizer

MODEL = "facebook/nllb-200-distilled-600M"
TEXT = "Hello, welcome to Soundme."

plain = AutoTokenizer.from_pretrained(MODEL)
plain.src_lang = "eng_Latn"
tok_setter = plain(TEXT)["input_ids"]
print("setter_full =", [plain.convert_ids_to_tokens([i])[0] for i in tok_setter])

kwarg = AutoTokenizer.from_pretrained(MODEL, src_lang="eng_Latn")
ids = kwarg(TEXT)["input_ids"]
print("kwarg_full =", [kwarg.convert_ids_to_tokens([i])[0] for i in ids])
print("kwarg_head =", kwarg.convert_ids_to_tokens(ids[:3]), "kwarg_tail =", kwarg.convert_ids_to_tokens(ids[-3:]))
