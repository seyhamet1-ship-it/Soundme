import time
import sys
import ctranslate2
import transformers
from app.providers import NllbTranslator

trans = NllbTranslator()
texts = [
    "Welcome to the Soundme Khmer dubbing studio.",
    "This benchmark measures real processing performance.",
    "Natural language processing has advanced significantly.",
    "The Mekong River flows through six countries in Southeast Asia.",
    "Cambodia's Angkor Wat is the largest religious monument in the world.",
    "Traditional Khmer music features unique instruments and melodies.",
    "Solar energy adoption is accelerating across developing nations.",
    "Rice paddies stretch across the Cambodian countryside."
]

# PyTorch Batched
tokenizer = trans._tokenizers["eng_Latn"]
target_id = tokenizer.convert_tokens_to_ids("khm_Khmr")
t0 = time.time()
batch = tokenizer(texts, padding=True, truncation=True, max_length=128, return_tensors="pt")
with __import__("torch").inference_mode():
    gen = trans.model.generate(**batch, forced_bos_token_id=target_id, max_new_tokens=100, num_beams=1, no_repeat_ngram_size=3)
pt_batch_res = [t.strip() for t in tokenizer.batch_decode(gen, skip_special_tokens=True)]
t_pt_batch = time.time() - t0
print(f"PyTorch Batched ({len(texts)} items): {t_pt_batch:.2f}s ({t_pt_batch/len(texts):.2f}s/item)", flush=True)

# CTranslate2 int8
model_dir = "tools/models/nllb-200-distilled-600M-ct2-int8"
ct2 = ctranslate2.Translator(model_dir, device="cpu", compute_type="int8", inter_threads=2, intra_threads=4)

t0 = time.time()
# Tokenize for NLLB: each sentence must have source lang tag or tokens
tokenized = [tokenizer.convert_ids_to_tokens(tokenizer.encode(t)) for t in texts]
results = ct2.translate_batch(
    tokenized,
    target_prefix=[["khm_Khmr"]] * len(texts),
    beam_size=1,
    max_decoding_length=100,
    no_repeat_ngram_size=3,
    max_batch_size=8
)
ct2_res = []
for r in results:
    clean = [t for t in r.hypotheses[0] if t not in {"khm_Khmr", "</s>"}]
    ct2_res.append(tokenizer.decode(tokenizer.convert_tokens_to_ids(clean)).strip())
t_ct2 = time.time() - t0
print(f"CTranslate2 int8 ({len(texts)} items): {t_ct2:.2f}s ({t_ct2/len(texts):.2f}s/item)", flush=True)

print("Sample comparison:")
for i in range(2):
    print(f"[{i+1}] {texts[i]}")
    print(f"  PyTorch: {pt_batch_res[i]}")
    print(f"  CT2:     {ct2_res[i]}")
