from __future__ import annotations

import asyncio
import logging
import math
import os
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Transcript:
    segments: list[Any]
    language: str
    language_probability: float


class WhisperASR:
    def __init__(self, model_name: str = "small") -> None:
        from faster_whisper import WhisperModel
        device = "cuda" if os.getenv("ASR_DEVICE", "cpu").lower() == "cuda" else "cpu"
        compute = "float16" if device == "cuda" else "int8"
        self.model = WhisperModel(model_name, device=device, compute_type=compute, cpu_threads=4)

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

    def transcribe(self, audio: Path, source_lang: str = "auto") -> Transcript:
        language = None if source_lang == "auto" else source_lang
        samples = self._load_wav(audio)
        audio_input = samples if samples is not None else str(audio)
        try:
            segments, info = self.model.transcribe(audio_input, language=language, vad_filter=True, beam_size=3)
            segs = list(segments)
        except ValueError as exc:
            if "max() iterable argument is empty" in str(exc):
                # Silero VAD found zero speech chunks -> no audible speech in audio
                return Transcript([], "en", 0.0)
            import logging as _logging
            _logging.getLogger("dubber.providers").warning(
                "VAD error (%s); retrying without vad_filter", exc
            )
            segments, info = self.model.transcribe(audio_input, language=language, vad_filter=False, beam_size=3)
            segs = list(segments)
        return Transcript(segs, info.language, float(info.language_probability))


class NllbTranslator:
    _model: Any = None
    _model_id: str | None = None
    _tokenizers: dict[str, Any] = {}
    _lock = __import__("threading").Lock()

    def __init__(self) -> None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        model_id = os.getenv("NLLB_MODEL", "facebook/nllb-200-distilled-600M")
        with NllbTranslator._lock:
            if NllbTranslator._model is None or NllbTranslator._model_id != model_id:
                NllbTranslator._model = AutoModelForSeq2SeqLM.from_pretrained(model_id, dtype=torch.float32).to("cpu")
                NllbTranslator._model.eval()
                NllbTranslator._model_id = model_id
                NllbTranslator._tokenizers = {}
            self.model = NllbTranslator._model
            if not NllbTranslator._tokenizers:
                # src_lang must be provided at tokenizer construction; setting the
                # attribute afterwards does not append the NLLB language token.
                for code in ("eng_Latn", "zho_Hans"):
                    NllbTranslator._tokenizers[code] = AutoTokenizer.from_pretrained(model_id, src_lang=code)

    def translate(self, text: str, source: str, target: str = "km") -> str:
        import torch
        from transformers import AutoTokenizer
        src_map = {"en": "eng_Latn", "zh": "zho_Hans", "km": "khm_Khmr"}
        tgt_map = {"en": "eng_Latn", "zh": "zho_Hans", "km": "khm_Khmr"}
        source_id = src_map.get(source, "eng_Latn")
        target_code = tgt_map.get(target, "khm_Khmr")
        with NllbTranslator._lock:
            if source_id not in NllbTranslator._tokenizers:
                model_id = os.getenv("NLLB_MODEL", "facebook/nllb-200-distilled-600M")
                NllbTranslator._tokenizers[source_id] = AutoTokenizer.from_pretrained(model_id, src_lang=source_id)
        tokenizer = NllbTranslator._tokenizers[source_id]
        batch = tokenizer(text, return_tensors="pt", truncation=True, max_length=384)
        target_id = tokenizer.convert_tokens_to_ids(target_code)
        with torch.inference_mode():
            # num_beams>1 degenerates into repetition loops with transformers 4.57 on this
            # M2M100/NLLB checkpoint; greedy decoding verified coherent Khmer output.
            result = self.model.generate(**batch, forced_bos_token_id=target_id, max_new_tokens=160, num_beams=1, no_repeat_ngram_size=3)
        return tokenizer.batch_decode(result, skip_special_tokens=True)[0].strip()


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
    _lock = asyncio.Lock()

    async def synthesize(self, text: str, voice: str, destination: Path, rate: str = "+0%") -> None:
        import edge_tts
        import subprocess
        import tempfile

        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".mp3", dir=destination.parent, delete=False) as temp:
            mp3_path = Path(temp.name)
        try:
            max_retries = 2
            async with self._lock:
                for attempt in range(max_retries + 1):
                    try:
                        await edge_tts.Communicate(text, voice=voice, rate=rate).save(str(mp3_path))
                        if mp3_path.is_file() and mp3_path.stat().st_size > 0:
                            break
                        raise RuntimeError("Edge TTS produced empty output file.")
                    except Exception as exc:
                        if attempt < max_retries:
                            await asyncio.sleep(1.0 * (attempt + 1))
                            continue
                        raise
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


class AzureTTSProvider:
    """Azure Speech fallback, enabled only when both Azure settings are configured."""

    def synthesize(self, text: str, voice: str, destination: Path) -> None:
        import azure.cognitiveservices.speech as speechsdk

        config = speechsdk.SpeechConfig(
            subscription=os.environ["AZURE_SPEECH_KEY"],
            region=os.environ["AZURE_SPEECH_REGION"],
        )
        config.speech_synthesis_voice_name = voice
        config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
        )
        result = speechsdk.SpeechSynthesizer(
            speech_config=config, audio_config=None
        ).speak_text_async(text).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted or not result.audio_data:
            raise RuntimeError(f"Azure Speech synthesis failed: {result.reason}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(result.audio_data)


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


def translate_khmer(text: str, source: str) -> tuple[str, str]:
    provider = os.getenv("TRANSLATION_PROVIDER", "nllb").strip().lower()
    if provider == "demo":
        if os.getenv("DEMO_MODE", "").strip().lower() not in {"1", "true", "yes"}:
            raise RuntimeError("Demo translation requires DEMO_MODE=1.")
        return DemoTranslator().translate(text, source), "demo-fixture"
    try:
        translated = NllbTranslator().translate(text, source)
    except Exception as exc:
        raise RuntimeError(f"NLLB translation failed; no fixture fallback is enabled: {exc}") from exc
    if not translated.strip():
        raise RuntimeError("NLLB translation returned empty text.")
    return translated, "nllb-local"


def tts_provider_health() -> dict[str, bool]:
    """Report configured provider readiness; Edge still gets a live network probe per job."""
    demo_mode = os.getenv("DEMO_MODE", "").strip().lower() in {"1", "true", "yes"}
    selected = os.getenv("TTS_PROVIDER", "auto").strip().lower()
    azure_ready = bool(os.getenv("AZURE_SPEECH_KEY") and os.getenv("AZURE_SPEECH_REGION"))
    mms_enabled = os.getenv("MMS_TTS_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
    return {
        "edge-tts": selected in {"auto", "edge", "edge-tts"},
        "azure": selected in {"auto", "azure", "azure-speech"} and azure_ready,
        "mms": selected in {"auto", "mms"} and mms_enabled,
        "demo": demo_mode and selected == "demo",
    }


def synthesize_khmer(text: str, voice: str, destination: Path, duration: float, seed: int = 1, rate: str = "+0%") -> str:
    demo_mode = os.getenv("DEMO_MODE", "").strip().lower() in {"1", "true", "yes"}
    selected = os.getenv("TTS_PROVIDER", "auto").strip().lower()
    if selected == "none":
        raise RuntimeError("TTS_UNAVAILABLE: no reachable TTS providers are configured.")
    if demo_mode and selected == "demo":
        DemoToneTTS().synthesize(destination, max(0.4, duration), seed)
        return "demo-tone-not-speech"
    clean_text = sanitize_text_for_tts(text)

    failures: list[str] = []
    providers = [selected] if selected not in {"", "auto"} else ["edge", "azure", "mms"]
    if "azure" in providers and not (os.getenv("AZURE_SPEECH_KEY") and os.getenv("AZURE_SPEECH_REGION")):
        providers.remove("azure")
    for provider in providers:
        try:
            if provider in {"edge", "edge-tts"}:
                asyncio.run(EdgeTTSProvider().synthesize(clean_text, voice, destination, rate=rate))
                return "edge-tts"
            if provider in {"azure", "azure-speech"}:
                AzureTTSProvider().synthesize(clean_text, voice, destination)
                return "azure-speech"
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
    raise RuntimeError(f"TTS_UNAVAILABLE: Edge TTS -> Azure Speech (if configured) -> MMS failed ({detail}).")
