import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

MODEL = "facebook/nllb-200-distilled-600M"
TEXT = "Hello, welcome to Soundme. This is a short English test sentence for dubbing."

tokenizer = AutoTokenizer.from_pretrained(MODEL, src_lang="eng_Latn")
print("tokenizer_ready", flush=True)
model = AutoModelForSeq2SeqLM.from_pretrained(MODEL, dtype=torch.float32).to("cpu")
model.eval()
print("model_ready", flush=True)
target_id = tokenizer.convert_tokens_to_ids("khm_Khmr")
print("target_id", target_id, "khm_token", tokenizer.convert_ids_to_tokens([target_id]), flush=True)

encoded = tokenizer(TEXT, return_tensors="pt", truncation=True, max_length=384)
print("encoded", encoded["input_ids"].shape, flush=True)

with torch.inference_mode():
    out = model.generate(**encoded, forced_bos_token_id=target_id, max_new_tokens=40, num_beams=1, do_sample=False)
print("generated", flush=True)
ids = out[0].tolist()
print("gen_ids", ids[:40], flush=True)
print("gen_tokens", tokenizer.convert_ids_to_tokens(ids)[:40], flush=True)
print("gen_text", tokenizer.decode(ids, skip_special_tokens=True)[:300], flush=True)

