import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

MODEL = "facebook/nllb-200-distilled-600M"
TEXT = "Hello, welcome to Soundme. This is a short English test sentence for dubbing."

tokenizer = AutoTokenizer.from_pretrained(MODEL, src_lang="eng_Latn")
model = AutoModelForSeq2SeqLM.from_pretrained(MODEL, torch_dtype=torch.float32).to("cpu")
model.eval()
target_id = tokenizer.convert_tokens_to_ids("khm_Khmr")

encoded = tokenizer(TEXT, return_tensors="pt", truncation=True, max_length=384)
tokens = tokenizer.convert_ids_to_tokens(encoded["input_ids"][0].tolist())
print(f"tail={tokens[-4:]}")
with torch.inference_mode():
    out = model.generate(**encoded, forced_bos_token_id=target_id, max_new_tokens=160, num_beams=2)
translated = tokenizer.batch_decode(out, skip_special_tokens=True)[0]
print(f"len={out.shape[1]} text={translated!r}")

# Same test for the Chinese source mapping used by the pipeline.
zh_tok = AutoTokenizer.from_pretrained(MODEL, src_lang="zho_Hans")
zh_enc = zh_tok("大家好，欢迎来到我们的频道。", return_tensors="pt", truncation=True, max_length=384)
print(f"zh_tail={zh_tok.convert_ids_to_tokens(zh_enc['input_ids'][0].tolist())[-4:]}")
