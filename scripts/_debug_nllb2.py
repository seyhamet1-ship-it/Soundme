import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

MODEL = "facebook/nllb-200-distilled-600M"
TEXT = "Hello, welcome to Soundme. This is a short English test sentence for dubbing."

tokenizer = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForSeq2SeqLM.from_pretrained(MODEL, torch_dtype=torch.float32).to("cpu")
model.eval()
target_id = tokenizer.convert_tokens_to_ids("khm_Khmr")
print("eos", tokenizer.eos_token_id, "bos", tokenizer.bos_token_id, "target", target_id)


def attempt(label: str, **kwargs) -> None:
    tokenizer.src_lang = "eng_Latn"
    encoded = tokenizer(TEXT, return_tensors="pt", truncation=True, max_length=384)
    ids = encoded["input_ids"][0].tolist()
    print(f"{label} input_tail={tokenizer.convert_ids_to_tokens(ids[-3:])}")
    with torch.inference_mode():
        out = model.generate(**encoded, forced_bos_token_id=target_id, **kwargs)
    text = tokenizer.batch_decode(out, skip_special_tokens=True)[0]
    print(f"{label} len={out.shape[1]} text={text[:160]!r}")


attempt("A_beams2_160", max_new_tokens=160, num_beams=2)
attempt("B_norepeat", max_new_tokens=160, num_beams=4, no_repeat_ngram_size=3, early_stopping=True)
attempt("C_greedy", max_new_tokens=160, num_beams=1)
