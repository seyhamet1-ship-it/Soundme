import sys
import time
import ctranslate2
from transformers import AutoTokenizer

model_dir = "tools/models/nllb-200-distilled-600M-ct2-int8"
model_id = "facebook/nllb-200-distilled-600M"

tok = AutoTokenizer.from_pretrained(model_id, src_lang="eng_Latn", local_files_only=True)
translator = ctranslate2.Translator(model_dir, device="cpu", compute_type="int8", inter_threads=2, intra_threads=4)

texts = [
    "Welcome to the Soundme Khmer dubbing studio.",
    "This benchmark measures real processing performance.",
    "Natural language processing has advanced significantly.",
    "The Mekong River flows through six countries in Southeast Asia.",
    "Cambodia's Angkor Wat is the largest religious monument in the world."
]

tokenized = [tok.tokenize(t) + ["</s>"] for t in texts]

t0 = time.time()
results = translator.translate_batch(
    tokenized,
    target_prefix=[["khm_Khmr"]] * len(texts),
    beam_size=1,
    max_decoding_length=160,
    no_repeat_ngram_size=3
)
elapsed = time.time() - t0

print(f"Translated {len(texts)} sentences in {elapsed:.3f}s ({elapsed/len(texts):.3f}s per sentence):")
for i, r in enumerate(results):
    hyp = r.hypotheses[0]
    # Remove language tag if present
    clean_tokens = [t for t in hyp if t != "khm_Khmr" and t != "</s>"]
    decoded = tok.decode(tok.convert_tokens_to_ids(clean_tokens))
    print(f"[{i+1}] {texts[i]} -> {decoded}")
