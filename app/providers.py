from __future__ import annotations

import asyncio
import contextlib
import contextvars
import datetime
import json
import logging
import math
import os
import struct
import threading
import time
import traceback
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

_translation_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "nllb_translation_context",
    default={"event_kind": "unspecified", "job_id": None, "progress": None},
)


def _utc_timestamp_ms() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _diagnostic_event(stage: str, event: str, **fields: Any) -> dict[str, Any]:
    context = _translation_context.get()
    payload: dict[str, Any] = {
        "timestamp": _utc_timestamp_ms(),
        "pid": os.getpid(),
        "thread_id": threading.get_ident(),
        "event_kind": context.get("event_kind", "unspecified"),
        "job_id": context.get("job_id"),
        "stage": stage,
        "event": event,
    }
    if context.get("progress") is not None:
        payload["progress"] = context.get("progress")
    payload.update(fields)
    return payload


def _log_diagnostic(stage: str, event: str, level: int = logging.INFO, **fields: Any) -> None:
    log.log(level, "NLLB_DIAGNOSTIC %s", json.dumps(_diagnostic_event(stage, event, **fields), ensure_ascii=False, sort_keys=True))


def _log_diagnostic_exception(stage: str, event: str, exc: Exception, **fields: Any) -> None:
    fields.update({"outcome": "error", "exception_type": type(exc).__name__, "traceback": traceback.format_exc()})
    log.error("NLLB_DIAGNOSTIC %s", json.dumps(_diagnostic_event(stage, event, **fields), ensure_ascii=False, sort_keys=True), exc_info=True)


def set_translation_diagnostic_context(job_id: str | None, progress: int | None, event_kind: str = "job_translation") -> Any:
    return _translation_context.set({"event_kind": event_kind, "job_id": job_id, "progress": progress})


def reset_translation_diagnostic_context(token: Any) -> None:
    _translation_context.reset(token)


# Force HuggingFace Hub / Transformers to use only locally cached models.
# All models (Whisper ASR, NLLB) are cached locally — no network access needed.
os.environ.setdefault("HF_HUB_LOCAL_FILES_ONLY", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


@dataclass
class Transcript:
    segments: list[Any]
    language: str
    language_probability: float


class WhisperASR:
    _models: dict[str, Any] = {}
    _lock = __import__("threading").Lock()
    _batched_pipe: Any = None

    def __init__(self, model_name: str = "small") -> None:
        from faster_whisper import WhisperModel
        device = "cuda" if os.getenv("ASR_DEVICE", "cpu").lower() == "cuda" else "cpu"
        compute = "float16" if device == "cuda" else "int8"
        cpu_threads = int(os.getenv("ASR_THREADS", "6"))
        key = f"{model_name}:{device}:{compute}:{cpu_threads}"
        with WhisperASR._lock:
            if key not in WhisperASR._models:
                WhisperASR._models[key] = WhisperModel(
                    model_name, device=device, compute_type=compute, cpu_threads=cpu_threads,
                    local_files_only=True,
                )
            self.model = WhisperASR._models[key]

    @staticmethod
    def _load_wav(path: Path) -> "np.ndarray | None":
        """Decode the pipeline's PCM WAV with stdlib wave.

        faster-whisper's own decoder calls av.open(metadata_errors=...), which the
        installed PyAV 19 no longer accepts; returning an ndarray bypasses it.
        """
        try:
            with wave.open(str(path), "rb") as handle:
                if handle.getsampwidth() != 2:
                    return None
                rate, channels = handle.getframerate(), handle.getnchannels()
                raw = handle.readframes(handle.getnframes())
        except Exception:
            return None
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        if rate != 16000 and data.size:
            new_size = max(1, round(data.size * 16000 / rate))
            data = np.interp(np.linspace(0, data.size - 1, new_size), np.arange(data.size), data).astype(np.float32)
        return data

    def transcribe(self, audio, source_lang: str = "auto", sample_rate: int | None = None) -> Transcript:
        import numpy as np

        language = None if source_lang == "auto" else source_lang

        if isinstance(audio, np.ndarray):
            if sample_rate is None:
                sample_rate = 16000
            if int(sample_rate) != 16000:
                raise ValueError("audio ndarray sample rate must be 16000 Hz")
            if audio.ndim != 1:
                raise ValueError("audio ndarray must be a 1-D mono array")
            if not np.issubdtype(audio.dtype, np.floating):
                raise ValueError("audio ndarray must have a floating-point dtype")
            if audio.size == 0:
                return Transcript([], "en" if language is None else language, 0.0)
            if not np.all(np.isfinite(audio)):
                raise ValueError("audio ndarray contains NaN or Inf values")
            audio_input = np.asarray(audio, dtype=np.float32).reshape(-1)
        else:
            samples = self._load_wav(audio)
            audio_input = samples if samples is not None else str(audio)
            if isinstance(audio_input, np.ndarray) and audio_input.size == 0:
                return Transcript([], "en" if language is None else language, 0.0)
        beam_size = int(os.getenv("ASR_BEAM_SIZE", "1"))
        use_batched = os.getenv("ASR_BATCHED", "1") != "0"
        import subprocess

        if use_batched:
            try:
                import types
                from faster_whisper import BatchedInferencePipeline
                with WhisperASR._lock:
                    if not hasattr(self, "_batched_pipe") or self._batched_pipe is None:
                        self._batched_pipe = BatchedInferencePipeline(model=self.model)
                batch_size = int(os.getenv("ASR_BATCH_SIZE", "8"))
                segments, info = self._batched_pipe.transcribe(
                    audio_input, batch_size=batch_size, language=language, word_timestamps=True, beam_size=beam_size
                )
                sentence_segs = []
                for s in segments:
                    if not hasattr(s, "words") or not s.words:
                        sentence_segs.append(s)
                        continue
                    cur = []
                    for w in s.words:
                        cur.append(w)
                        word_clean = w.word.strip()
                        if any(word_clean.endswith(p) for p in [".", "!", "?", "。", "！", "？"]):
                            sentence_segs.append(types.SimpleNamespace(
                                start=cur[0].start, end=cur[-1].end, text="".join(x.word for x in cur).strip()
                            ))
                            cur = []
                    if cur:
                        sentence_segs.append(types.SimpleNamespace(
                            start=cur[0].start, end=cur[-1].end, text="".join(x.word for x in cur).strip()
                        ))
                segs = sentence_segs if sentence_segs else list(segments)
                if segs:
                    return Transcript(segs, info.language, float(info.language_probability))
            except Exception as b_exc:
                log.warning("BatchedInferencePipeline failed (%s); falling back to standard transcribe", b_exc)

        try:
            segments, info = self.model.transcribe(audio_input, language=language, vad_filter=True, beam_size=beam_size)
            segs = list(segments)
            if not segs:
                # VAD can reject music-backed vocals even when Whisper can
                # transcribe them.  Retry once without VAD; do not change the
                # normal VAD-first path or make this an unbounded retry loop.
                log.info("VAD produced no ASR segments; retrying once without vad_filter")
                segments, info = self.model.transcribe(audio_input, language=language, vad_filter=False, beam_size=beam_size)
                segs = list(segments)
        except ValueError as exc:
            if "max() iterable argument is empty" in str(exc):
                return Transcript([], "en", 0.0)
            log.warning("VAD error (%s); retrying without vad_filter", exc)
            segments, info = self.model.transcribe(audio_input, language=language, vad_filter=False, beam_size=beam_size)
            segs = list(segments)
        return Transcript(segs, info.language, float(info.language_probability))

    def transcribe_chunked(self, *args, **kwargs):
        from app.asr_chunking import ChunkedASR
        audio = args[0] if args else kwargs.pop("audio")
        rest = list(args[1:])
        init_keys = {"chunk_duration", "overlap", "max_retries", "beam_size", "max_new_tokens"}
        init_kwargs = {k: v for k, v in kwargs.items() if k in init_keys}
        asr = ChunkedASR(self, **init_kwargs)
        call_kwargs = {k: v for k, v in kwargs.items() if k not in init_keys}
        if "source_lang" not in call_kwargs and rest:
            call_kwargs["source_lang"] = rest.pop(0)
        if rest and "source_lang" not in call_kwargs:
            call_kwargs["source_lang"] = rest.pop(0)
        return asr.transcribe(audio, **call_kwargs)

    transcribe_chunks = transcribe_chunked
    transcribe_long = transcribe_chunked
    transcribe_long_form = transcribe_chunked


def _local_ct2_model_dir(source: Path) -> Path:
    """Copy a CT2 model from a slow/virtual drive to a local cache once.

    This never moves or modifies the original model files; it only creates a
    local read cache so repeated process starts avoid Google Drive/OneDrive
    latency.  Local sources are used directly.
    """
    try:
        source = source.resolve()
    except Exception:
        return source
    lowered = str(source).lower()
    is_virtual = any(token in lowered for token in (
        "my drive", "google drive", "onedrive", "dropbox", "cloudstorage",
    ))
    if not is_virtual:
        return source
    import shutil
    import tempfile
    cache_root = Path(os.getenv("LOCALAPPDATA", tempfile.gettempdir())) / "Soundme" / "models"
    cache_dir = cache_root / source.name
    if not (cache_dir / "model.bin").exists():
        cache_root.mkdir(parents=True, exist_ok=True)
        temp_dir = cache_dir.with_name(cache_dir.name + ".tmp")
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.copytree(source, temp_dir)
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        temp_dir.rename(cache_dir)
    return cache_dir

class NllbTranslator:
    _model: Any = None
    _ct2_translator: Any = None
    _model_id: str | None = None
    _tokenizer: Any = None
    _lock = __import__("threading").Lock()

    def __init__(self) -> None:
        model_id = os.getenv("NLLB_MODEL", "facebook/nllb-200-distilled-600M")
        context = _translation_context.get()
        stage = "startup_warmup" if context.get("event_kind") == "startup_warmup" else "nllb_constructor"
        constructor_start = time.perf_counter()
        _log_diagnostic(stage, "constructor_start", model_already_cached=bool(NllbTranslator._ct2_translator is not None or NllbTranslator._model is not None), tokenizer_already_cached=NllbTranslator._tokenizer is not None)
        lock_wait_start = time.perf_counter()
        NllbTranslator._lock.acquire()
        lock_wait_ms = round((time.perf_counter() - lock_wait_start) * 1000, 3)
        lock_held_start = time.perf_counter()
        try:
            ct2_dir = _local_ct2_model_dir(Path(__file__).resolve().parents[1] / "tools" / "models" / "nllb-200-distilled-600M-ct2-int8")
            if ct2_dir.is_dir() and (ct2_dir / "model.bin").exists() and os.getenv("USE_CT2", "1") != "0":
                if NllbTranslator._ct2_translator is None:
                    ct2_start = time.perf_counter()
                    try:
                        import ctranslate2
                        NllbTranslator._ct2_translator = ctranslate2.Translator(str(ct2_dir), device="cpu", compute_type="int8", inter_threads=2, intra_threads=4)
                        _log_diagnostic(stage, "ct2_model_load_complete", elapsed_ms=round((time.perf_counter() - ct2_start) * 1000, 3), model_already_cached=False)
                    except Exception as exc:
                        _log_diagnostic_exception(stage, "ct2_model_load_error", exc)
                        log.warning("CTranslate2 init failed (%s); falling back to PyTorch", exc)
                        NllbTranslator._ct2_translator = None
            if NllbTranslator._ct2_translator is None:
                if NllbTranslator._model is None or NllbTranslator._model_id != model_id:
                    model_start = time.perf_counter()
                    try:
                        import torch
                        from transformers import AutoModelForSeq2SeqLM
                        _log_diagnostic(stage, "pytorch_model_load_start", model_already_cached=False)
                        NllbTranslator._model = AutoModelForSeq2SeqLM.from_pretrained(model_id, dtype=torch.float32, local_files_only=True).to("cpu")
                        NllbTranslator._model.eval()
                        NllbTranslator._model_id = model_id
                        _log_diagnostic(stage, "pytorch_model_load_complete", elapsed_ms=round((time.perf_counter() - model_start) * 1000, 3), model_already_cached=False)
                    except Exception as exc:
                        _log_diagnostic_exception(stage, "pytorch_model_load_error", exc)
                        raise
                else:
                    _log_diagnostic(stage, "pytorch_model_already_cached", model_already_cached=True)
                self.model = NllbTranslator._model
            if NllbTranslator._tokenizer is None:
                tokenizer_start = time.perf_counter()
                try:
                    from transformers import AutoTokenizer
                    _log_diagnostic(stage, "tokenizer_load_start", tokenizer_already_cached=False, load_path="constructor")
                    NllbTranslator._tokenizer = AutoTokenizer.from_pretrained(model_id, local_files_only=True)
                    _log_diagnostic(stage, "tokenizer_load_complete", elapsed_ms=round((time.perf_counter() - tokenizer_start) * 1000, 3), tokenizer_already_cached=False, load_path="constructor")
                except Exception as exc:
                    _log_diagnostic_exception(stage, "tokenizer_load_error", exc, tokenizer_already_cached=False, load_path="constructor")
                    raise
            else:
                _log_diagnostic(stage, "tokenizer_already_cached", tokenizer_already_cached=True, load_path="constructor")
            _log_diagnostic(stage, "constructor_lock_held_complete", lock_wait_ms=lock_wait_ms, lock_held_ms=round((time.perf_counter() - lock_held_start) * 1000, 3), model_already_cached=bool(NllbTranslator._ct2_translator is not None or NllbTranslator._model is not None), tokenizer_already_cached=NllbTranslator._tokenizer is not None)
        except Exception as exc:
            _log_diagnostic_exception(stage, "constructor_error", exc, elapsed_ms=round((time.perf_counter() - constructor_start) * 1000, 3), lock_wait_ms=lock_wait_ms)
            raise
        finally:
            NllbTranslator._lock.release()
        _log_diagnostic(stage, "constructor_complete", elapsed_ms=round((time.perf_counter() - constructor_start) * 1000, 3), lock_wait_ms=lock_wait_ms, model_already_cached=bool(NllbTranslator._ct2_translator is not None or NllbTranslator._model is not None), tokenizer_already_cached=NllbTranslator._tokenizer is not None)

    def translate_batch(self, texts: list[str], source: str, target: str = "km", batch_size: int = 8) -> list[str]:
        if not texts:
            return []
        src_map = {"en": "eng_Latn", "zh": "zho_Hans", "km": "khm_Khmr"}
        tgt_map = {"en": "eng_Latn", "zh": "zho_Hans", "km": "khm_Khmr"}
        source_id = src_map.get(source, "eng_Latn")
        target_code = tgt_map.get(target, "khm_Khmr")

        context = _translation_context.get()
        stage = "startup_warmup" if context.get("event_kind") == "startup_warmup" else "nllb_translate_batch"
        batch_start = time.perf_counter()
        _log_diagnostic(stage, "translate_batch_start", segment_count=len(texts), batch_size=batch_size, model_already_cached=bool(NllbTranslator._ct2_translator is not None or NllbTranslator._model is not None), tokenizer_already_cached=NllbTranslator._tokenizer is not None)
        lock_wait_start = time.perf_counter()
        NllbTranslator._lock.acquire()
        lock_wait_ms = round((time.perf_counter() - lock_wait_start) * 1000, 3)
        lock_held_start = time.perf_counter()
        try:
            if NllbTranslator._tokenizer is None:
                tokenizer_start = time.perf_counter()
                try:
                    from transformers import AutoTokenizer
                    model_id = os.getenv("NLLB_MODEL", "facebook/nllb-200-distilled-600M")
                    _log_diagnostic(stage, "tokenizer_load_start", tokenizer_already_cached=False, load_path="lazy")
                    NllbTranslator._tokenizer = AutoTokenizer.from_pretrained(model_id, local_files_only=True)
                    _log_diagnostic(stage, "tokenizer_load_complete", elapsed_ms=round((time.perf_counter() - tokenizer_start) * 1000, 3), tokenizer_already_cached=False, load_path="lazy")
                except Exception as exc:
                    _log_diagnostic_exception(stage, "tokenizer_load_error", exc, tokenizer_already_cached=False, load_path="lazy")
                    raise
            else:
                _log_diagnostic(stage, "tokenizer_already_cached", tokenizer_already_cached=True, load_path="lazy")
            tokenizer = NllbTranslator._tokenizer
            if getattr(tokenizer, 'src_lang', None) != source_id:
                tokenizer.src_lang = source_id
            _log_diagnostic(stage, "translate_lock_setup_complete", lock_wait_ms=lock_wait_ms, lock_held_ms=round((time.perf_counter() - lock_held_start) * 1000, 3), segment_count=len(texts), batch_size=batch_size)

            if NllbTranslator._ct2_translator is not None:
                results_out = []
                for batch_index, i in enumerate(range(0, len(texts), batch_size)):
                    chunk = texts[i:i+batch_size]
                    inference_start = time.perf_counter()
                    _log_diagnostic(stage, "ct2_inference_start", batch_index=batch_index, segment_count=len(chunk), batch_size=batch_size)
                    try:
                        tokenized = [tokenizer.convert_ids_to_tokens(tokenizer.encode(t)) for t in chunk]
                        res_batch = NllbTranslator._ct2_translator.translate_batch(
                            tokenized,
                            target_prefix=[[target_code]] * len(chunk),
                            beam_size=1,
                            max_decoding_length=160,
                            no_repeat_ngram_size=3,
                            max_batch_size=batch_size
                        )
                        for r in res_batch:
                            clean = [t for t in r.hypotheses[0] if t not in {target_code, "</s>"}]
                            results_out.append(tokenizer.decode(tokenizer.convert_tokens_to_ids(clean)).strip())
                        _log_diagnostic(stage, "ct2_inference_complete", batch_index=batch_index, segment_count=len(chunk), batch_size=batch_size, elapsed_ms=round((time.perf_counter() - inference_start) * 1000, 3))
                    except Exception as exc:
                        _log_diagnostic_exception(stage, "ct2_inference_error", exc, batch_index=batch_index, segment_count=len(chunk), batch_size=batch_size, elapsed_ms=round((time.perf_counter() - inference_start) * 1000, 3))
                        raise
                _log_diagnostic(stage, "translate_batch_complete", elapsed_ms=round((time.perf_counter() - batch_start) * 1000, 3), segment_count=len(texts), batch_size=batch_size, lock_wait_ms=lock_wait_ms, lock_held_ms=round((time.perf_counter() - lock_held_start) * 1000, 3), model_already_cached=True, tokenizer_already_cached=True)
                return results_out
        except Exception as exc:
            _log_diagnostic_exception(stage, "translate_batch_error", exc, elapsed_ms=round((time.perf_counter() - batch_start) * 1000, 3), segment_count=len(texts), batch_size=batch_size, lock_wait_ms=lock_wait_ms)
            raise
        finally:
            NllbTranslator._lock.release()

        import torch
        target_id = tokenizer.convert_tokens_to_ids(target_code)
        results_out = []
        try:
            for batch_index, i in enumerate(range(0, len(texts), batch_size)):
                chunk = texts[i:i+batch_size]
                batch = tokenizer(chunk, padding=True, truncation=True, max_length=256, return_tensors="pt")
                inference_start = time.perf_counter()
                _log_diagnostic(stage, "pytorch_inference_start", batch_index=batch_index, segment_count=len(chunk), batch_size=batch_size)
                try:
                    with torch.inference_mode():
                        result = NllbTranslator._model.generate(
                            **batch, forced_bos_token_id=target_id, max_new_tokens=160, num_beams=1, no_repeat_ngram_size=3
                        )
                    _log_diagnostic(stage, "pytorch_inference_complete", batch_index=batch_index, segment_count=len(chunk), batch_size=batch_size, elapsed_ms=round((time.perf_counter() - inference_start) * 1000, 3))
                except Exception as exc:
                    _log_diagnostic_exception(stage, "pytorch_inference_error", exc, batch_index=batch_index, segment_count=len(chunk), batch_size=batch_size, elapsed_ms=round((time.perf_counter() - inference_start) * 1000, 3))
                    raise
                decoded = tokenizer.batch_decode(result, skip_special_tokens=True)
                results_out.extend([d.strip() for d in decoded])
        except Exception as exc:
            _log_diagnostic_exception(stage, "translate_batch_error", exc, elapsed_ms=round((time.perf_counter() - batch_start) * 1000, 3), segment_count=len(texts), batch_size=batch_size, lock_wait_ms=lock_wait_ms)
            raise
        _log_diagnostic(stage, "translate_batch_complete", elapsed_ms=round((time.perf_counter() - batch_start) * 1000, 3), segment_count=len(texts), batch_size=batch_size, lock_wait_ms=lock_wait_ms, model_already_cached=True, tokenizer_already_cached=True)
        return results_out

    def translate(self, text: str, source: str, target: str = "km") -> str:
        return self.translate_batch([text], source, target)[0]


class DemoTranslator:
    """Deterministic fixture: Khmer phrase mapping for known demo lines, not an MT system."""
    _phrases = {
        "hello everyone, welcome to our channel.": "សួស្តីអ្នកទាំងអស់គ្នា សូមស្វាគមន៍មកកាន់ឆានែលរបស់យើង។",
        "today we will introduce a new product.": "ថ្ងៃនេះយើងនឹងណែនាំផលិតផលថ្មីមួយ។",
        "the price is one hundred and twenty dollars.": "តម្លៃគឺមួយរយម្ភៃដុល្លារ។",
        "大家好，欢迎来到我们的频道。": "សួស្តីអ្នកទាំងអស់គ្នា សូមស្វាគមន៍មកកាន់ឆានែលរបស់យើង។",
        "今天我们要介绍一个新的产品。": "ថ្ងៃនេះយើងនឹងណែនាំផលិតផលថ្មីមួយ។",
        "价格是一百二十元。": "តម្លៃគឺមួយរយម្ភៃយន់។",
    }

    def translate(self, text: str, source: str) -> str:
        key = text.strip().lower()
        if key in self._phrases:
            return self._phrases[key]
        return "សូមពិនិត្យការបកប្រែនេះ" if not text.strip() else "សូមពិនិត្យ៖ " + text.strip()


def sanitize_text_for_tts(text: str) -> str:
    """Sanitize and validate text before synthesis.
    
    Strips whitespace and punctuation.
    Raises ValueError('TTS_EMPTY_TEXT: ...') if no speakable characters remain.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("TTS_EMPTY_TEXT: Text is empty.")
    has_letters = any(c.isalnum() for c in cleaned)
    if not has_letters:
        raise ValueError(f"TTS_EMPTY_TEXT: Text has no speakable characters: {cleaned!r}")
    return cleaned


class EdgeTTSProvider:
    _sem = __import__("threading").Semaphore(int(os.getenv("EDGE_TTS_CONCURRENCY", "2")))

    async def synthesize(self, text: str, voice: str, destination: Path, rate: str = "+0%") -> None:
        import edge_tts
        import subprocess
        import tempfile
        import random
        import time

        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".mp3", dir=destination.parent, delete=False) as temp:
            mp3_path = Path(temp.name)
        try:
            max_retries = int(os.getenv("EDGE_TTS_MAX_RETRIES", "3"))
            with EdgeTTSProvider._sem:
                time.sleep(random.uniform(0.05, 0.15))
                for attempt in range(max_retries + 1):
                    try:
                        await edge_tts.Communicate(text, voice=voice, rate=rate).save(str(mp3_path))
                        if mp3_path.is_file() and mp3_path.stat().st_size > 0:
                            break
                        raise RuntimeError("Edge TTS produced empty output file.")
                    except Exception as exc:
                        if attempt < max_retries:
                            await asyncio.sleep(0.3 * (attempt + 1) + random.uniform(0.05, 0.15))
                            continue
                        raise
            # Convert MP3 to 24kHz mono PCM WAV, trimming excessive trailing silence padding added by Edge-TTS
            trim_filter = "silenceremove=stop_periods=-1:stop_duration=0.15:stop_threshold=-40dB"
            res = subprocess.run(
                [os.getenv("FFMPEG", "ffmpeg"), "-y", "-i", str(mp3_path), "-af", trim_filter, "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(destination)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if res.returncode != 0 or not destination.exists() or destination.stat().st_size < 1000:
                # Fallback without silenceremove if filtering produced an unusable file
                subprocess.run(
                    [os.getenv("FFMPEG", "ffmpeg"), "-y", "-i", str(mp3_path), "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(destination)],
                    check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
        finally:
            mp3_path.unlink(missing_ok=True)


class MMSTTSProvider:
    """Offline-capable Khmer neural TTS using facebook/mms-tts-khm."""

    _tokenizer: Any = None
    _model: Any = None
    _lock = __import__("threading").Lock()

    def synthesize(self, text: str, destination: Path) -> None:
        import torch
        from transformers import AutoTokenizer, VitsModel

        model_id = os.getenv("MMS_TTS_MODEL", "facebook/mms-tts-khm")
        with self._lock:
            if self._model is None:
                self._tokenizer = AutoTokenizer.from_pretrained(model_id)
                self._model = VitsModel.from_pretrained(model_id).eval()
        encoded = self._tokenizer(text, return_tensors="pt")
        token_count = encoded.input_ids.shape[-1]
        if token_count == 0:
            raise ValueError("TTS_EMPTY_TEXT: Text has zero speakable phonetic tokens for MMS.")
        
        max_chunk_tokens = 350
        if token_count > max_chunk_tokens:
            sentences = [s.strip() for s in text.replace("។", "។\n").splitlines() if s.strip()]
            all_samples = []
            for s in sentences:
                s_enc = self._tokenizer(s, return_tensors="pt")
                if s_enc.input_ids.shape[-1] == 0:
                    continue
                with torch.inference_mode():
                    s_out = self._model(**s_enc).waveform.squeeze().cpu().numpy()
                all_samples.append(s_out)
            if all_samples:
                samples = np.concatenate(all_samples)
            else:
                raise ValueError("TTS_EMPTY_TEXT: All chunks produced zero tokens.")
        else:
            with torch.inference_mode():
                samples = self._model(**encoded).waveform.squeeze().cpu().numpy()
        peak = float(np.max(np.abs(samples)))
        if not np.isfinite(samples).all() or peak <= 0:
            raise RuntimeError("MMS produced empty or non-finite audio.")
        samples = np.clip(samples * min(1.0, 0.89 / peak), -0.89, 0.89)
        pcm = (samples * 32767).astype("<i2")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self._model.config.sampling_rate)
            output.writeframes(pcm.tobytes())


MODELS_READY = False

def is_models_ready() -> bool:
    global MODELS_READY
    return MODELS_READY

def warm_models() -> bool:
    global MODELS_READY
    token = set_translation_diagnostic_context(None, None, "startup_warmup")
    warm_start = time.perf_counter()
    _log_diagnostic("startup_warmup", "warmup_start")
    try:
        asr = WhisperASR(os.getenv("ASR_MODEL", "small"))
        _ = asr.model
        trans = NllbTranslator()
        _ = trans.translate_batch(["SoundMe"], "en", "km")
        MODELS_READY = True
        _log_diagnostic("startup_warmup", "warmup_complete", elapsed_ms=round((time.perf_counter() - warm_start) * 1000, 3))
        log.info("All pipeline models are warmed and ready.")
        return True
    except Exception as exc:
        _log_diagnostic_exception("startup_warmup", "warmup_error", exc, elapsed_ms=round((time.perf_counter() - warm_start) * 1000, 3))
        log.warning("Model warming non-fatal error: %s", exc)
        MODELS_READY = False
        return False
    finally:
        reset_translation_diagnostic_context(token)

def get_tts_provider_order() -> list[str]:
    custom = os.getenv("TTS_PROVIDER_ORDER")
    if custom:
        return [p.strip().lower() for p in custom.split(",") if p.strip()]
    selected = os.getenv("TTS_PROVIDER", "auto").strip().lower()
    if selected not in {"", "auto"}:
        return [selected]
    azure_ready = bool(os.getenv("AZURE_SPEECH_KEY") and os.getenv("AZURE_SPEECH_REGION"))
    edge_allowed = os.getenv("ENABLE_EDGE_TTS", "1").strip().lower() not in {"0", "false", "no"}
    mms_allowed = os.getenv("ENABLE_MMS_TTS", "1").strip().lower() not in {"0", "false", "no"}
    order = []
    if azure_ready:
        order.append("azure")
    if edge_allowed:
        order.append("edge")
    if mms_allowed:
        order.append("mms")
    return order or ["edge", "mms"]

class AzureTTSProvider:
    """Azure Speech primary/fallback, enabled when AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are configured."""

    def synthesize(self, text: str, voice: str, destination: Path, rate: str = "+0%") -> None:
        import azure.cognitiveservices.speech as speechsdk
        from app.storage import get_monthly_azure_chars, record_azure_chars

        cap = int(os.getenv("AZURE_MONTHLY_CHAR_CAP", "450000"))
        used = get_monthly_azure_chars()
        if used + len(text) > cap:
            log.warning("Azure Speech monthly cap reached (%d / %d chars used); stopping Azure calls", used, cap)
            raise RuntimeError(f"AZURE_CAP_REACHED: Monthly character limit ({cap}) reached. Used: {used} chars.")

        config = speechsdk.SpeechConfig(
            subscription=os.environ["AZURE_SPEECH_KEY"],
            region=os.environ["AZURE_SPEECH_REGION"],
        )
        config.speech_synthesis_voice_name = voice
        config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
        )
        synth = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)

        if rate and rate.strip() not in {"+0%", "0%", "+0"}:
            r_str = rate.strip()
            ssml = (
                f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="km-KH">'
                f'<voice name="{voice}"><prosody rate="{r_str}">{text}</prosody></voice></speak>'
            )
            result = synth.speak_ssml_async(ssml).get()
        else:
            result = synth.speak_text_async(text).get()

        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted or not result.audio_data:
            raise RuntimeError(f"Azure Speech synthesis failed: {result.reason}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(result.audio_data)
        record_azure_chars(len(text))


class DemoToneTTS:
    """Audible deterministic pipeline fixture; deliberately not claimed as Khmer speech."""
    def synthesize(self, destination: Path, duration: float, seed: int = 1) -> None:
        rate, frames = 24000, max(1, int(duration * 24000))
        rng = np.random.default_rng(seed)
        t = np.arange(frames, dtype=np.float32) / rate
        envelope = (0.18 + 0.10 * np.sin(2 * np.pi * 3.2 * t)) * (0.55 + 0.45 * (np.sin(2 * np.pi * 5 * t) > 0))
        signal = envelope * np.sin(2 * np.pi * (155 + seed % 33) * t) + 0.025 * rng.standard_normal(frames)
        signal = np.clip(signal / max(1.0, np.max(np.abs(signal)) / 0.89), -0.89, 0.89)
        pcm = (signal * 32767).astype("<i2").tobytes()
        with wave.open(str(destination), "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate); out.writeframes(pcm)


def translate_khmer_batch(texts: list[str], source: str) -> tuple[list[str], str]:
    start = time.perf_counter()
    context = _translation_context.get()
    stage = "startup_warmup" if context.get("event_kind") == "startup_warmup" else "translate_khmer_batch"
    _log_diagnostic(stage, "wrapper_start", segment_count=len(texts), batch_size=len(texts))
    if not texts:
        _log_diagnostic(stage, "wrapper_complete", elapsed_ms=0.0, segment_count=0, batch_size=0)
        return [], "none"
    provider = os.getenv("TRANSLATION_PROVIDER", "nllb").strip().lower()
    if provider == "demo":
        try:
            if os.getenv("DEMO_MODE", "").strip().lower() not in {"1", "true", "yes"}:
                raise RuntimeError("Demo translation requires DEMO_MODE=1.")
            translator = DemoTranslator()
            result = ([translator.translate(t, source) for t in texts], "demo-fixture")
            _log_diagnostic(stage, "wrapper_complete", elapsed_ms=round((time.perf_counter() - start) * 1000, 3), segment_count=len(texts), batch_size=len(texts), provider="demo-fixture")
            return result
        except Exception as exc:
            _log_diagnostic_exception(stage, "wrapper_error", exc, elapsed_ms=round((time.perf_counter() - start) * 1000, 3), segment_count=len(texts), batch_size=len(texts))
            raise
    try:
        translated = NllbTranslator().translate_batch(texts, source)
    except Exception as exc:
        _log_diagnostic_exception(stage, "wrapper_error", exc, elapsed_ms=round((time.perf_counter() - start) * 1000, 3), segment_count=len(texts), batch_size=len(texts))
        raise RuntimeError(f"NLLB translation failed; no fixture fallback is enabled: {exc}") from exc
    p_name = "nllb-ct2-int8" if NllbTranslator._ct2_translator is not None else "nllb-local"
    _log_diagnostic(stage, "wrapper_complete", elapsed_ms=round((time.perf_counter() - start) * 1000, 3), segment_count=len(texts), batch_size=len(texts), provider=p_name)
    return translated, p_name


def translate_khmer(text: str, source: str) -> tuple[str, str]:
    batch, p = translate_khmer_batch([text], source)
    if not batch or not batch[0].strip():
        raise RuntimeError("NLLB translation returned empty text.")
    return batch[0], p


def tts_provider_health() -> dict[str, Any]:
    """Report configured provider readiness, Azure usage, and active provider."""
    demo_mode = os.getenv("DEMO_MODE", "").strip().lower() in {"1", "true", "yes"}
    selected = os.getenv("TTS_PROVIDER", "auto").strip().lower()
    azure_ready = bool(os.getenv("AZURE_SPEECH_KEY") and os.getenv("AZURE_SPEECH_REGION"))
    mms_enabled = os.getenv("ENABLE_MMS_TTS", "1").strip().lower() not in {"0", "false", "no"}
    edge_enabled = os.getenv("ENABLE_EDGE_TTS", "1").strip().lower() not in {"0", "false", "no"}
    order = get_tts_provider_order()
    primary = order[0] if order else "none"

    from app.storage import get_monthly_azure_chars
    azure_chars = get_monthly_azure_chars()
    azure_cap = int(os.getenv("AZURE_MONTHLY_CHAR_CAP", "450000"))

    return {
        "primary": primary,
        "order": order,
        "edge-tts": edge_enabled and ("edge" in order or "edge-tts" in order),
        "azure": azure_ready and ("azure" in order or "azure-speech" in order),
        "mms": mms_enabled and ("mms" in order),
        "demo": demo_mode and selected == "demo",
        "azure_chars_used_month": azure_chars,
        "azure_monthly_cap": azure_cap,
    }


def synthesize_khmer(
    text: str,
    voice: str,
    destination: Path,
    duration: float,
    seed: int = 1,
    rate: str = "+0%",
    forced_provider: str | None = None,
) -> str:
    demo_mode = os.getenv("DEMO_MODE", "").strip().lower() in {"1", "true", "yes"}
    selected = os.getenv("TTS_PROVIDER", "auto").strip().lower()
    if selected == "none":
        raise RuntimeError("TTS_UNAVAILABLE: no reachable TTS providers are configured.")
    if demo_mode and selected == "demo":
        DemoToneTTS().synthesize(destination, max(0.4, duration), seed)
        return "demo-tone-not-speech"
    clean_text = sanitize_text_for_tts(text)

    failures: list[str] = []
    if forced_provider:
        providers = [forced_provider]
    elif selected not in {"", "auto"}:
        providers = [selected]
    else:
        providers = get_tts_provider_order()

    for provider in providers:
        try:
            if provider in {"azure", "azure-speech"}:
                AzureTTSProvider().synthesize(clean_text, voice, destination, rate=rate)
                return "azure-speech"
            if provider in {"edge", "edge-tts"}:
                asyncio.run(EdgeTTSProvider().synthesize(clean_text, voice, destination, rate=rate))
                return "edge-tts"
            if provider == "mms":
                MMSTTSProvider().synthesize(clean_text, destination)
                return "mms-facebook/mms-tts-khm"
            if provider == "demo" and demo_mode:
                DemoToneTTS().synthesize(destination, max(0.4, duration), seed)
                return "demo-tone-not-speech"
            failures.append(f"{provider}:unsupported")
        except Exception as exc:
            destination.unlink(missing_ok=True)
            failures.append(f"{provider}:{type(exc).__name__}:{str(exc)[:180]}")
    detail = "; ".join(failures) or "no provider is enabled"
    order_str = " -> ".join(providers)
    raise RuntimeError(f"TTS_UNAVAILABLE: {order_str} failed ({detail}).")
