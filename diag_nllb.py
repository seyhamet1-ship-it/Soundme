import torch
import transformers
import sentencepiece
import tokenizers

print('torch', torch.__version__)
print('transformers', transformers.__version__)
print('sentencepiece', sentencepiece.__version__)
print('tokenizers', tokenizers.__version__)
print('cuda', torch.cuda.is_available())
print('cuda_devices', torch.cuda.device_count())

from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

text = 'Hello, how are you today?'
tok = AutoTokenizer.from_pretrained('facebook/nllb-200-distilled-600M')
model = AutoModelForSeq2SeqLM.from_pretrained('facebook/nllb-200-distilled-600M')
model.eval()
print('MODEL_LOAD=PASS')
inputs = tok(text, return_tensors='pt', src_lang='eng_Latn')
generated = model.generate(**inputs, forced_bos_token_id=tok.lang_code_to_id['khm_Khmr'])
out = tok.batch_decode(generated, skip_special_tokens=True)[0]
print('RESULT:', out)
