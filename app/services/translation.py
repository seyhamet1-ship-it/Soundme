from __future__ import annotations

import hashlib
import logging
import os
import random
import threading
import time
from typing import Any

from app.config import (
    TRANSLATION_BACKOFF_BASE,
    TRANSLATION_FALLBACK_PROVIDER,
    TRANSLATION_LOCAL_MODEL,
    TRANSLATION_MAX_RETRIES,
    TRANSLATION_MIN_DELAY,
    TRANSLATION_OFFLINE,
    TRANSLATION_PROVIDER,
    TRANSLATION_TIMEOUT,
)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency path
    from deep_translator import GoogleTranslator, MyMemoryTranslator
    from deep_translator.exceptions import LanguageNotSupportedException, RequestError, TooManyRequests
except Exception:  # pragma: no cover - fallback for environments without deep-translator
    GoogleTranslator = None
    MyMemoryTranslator = None
    LanguageNotSupportedException = Exception
    RequestError = Exception
    TooManyRequests = Exception


class TranslationError(RuntimeError):
    pass


class TranslationConfigurationError(TranslationError):
    pass


class TranslationRateLimitError(TranslationError):
    pass


class TranslationUnsupportedLanguageError(TranslationError):
    pass


class TranslationOfflineModeError(TranslationError):
    pass


class TranslationCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, str] = {}

    @staticmethod
    def _build_key(source: str, target: str, text: str) -> str:
        payload = f"{source}:{target}:{text.strip()}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def get(self, source: str, target: str, text: str) -> str | None:
        key = self._build_key(source, target, text)
        with self._lock:
            return self._store.get(key)

    def set(self, source: str, target: str, text: str, translated: str) -> None:
        key = self._build_key(source, target, text)
        with self._lock:
            self._store[key] = translated


class LocalTranslationProvider:
    _tokenizer: Any = None
    _model: Any = None
    _lock = threading.Lock()

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = (model_name or TRANSLATION_LOCAL_MODEL or "facebook/nllb-200-distilled-600M").strip()

    @staticmethod
    def normalize_language_code(value: str | None, *, default: str = "en") -> str:
        if not value:
            return default
        normalized = str(value).strip().lower().replace("_", "-")
        if not normalized:
            return default
        mapping = {
            "en": "en",
            "en-us": "en",
            "en-gb": "en",
            "fr": "fr",
            "fr-fr": "fr",
            "es": "es",
            "es-es": "es",
            "de": "de",
            "de-de": "de",
            "it": "it",
            "it-it": "it",
            "pt": "pt",
            "pt-br": "pt",
            "ja": "ja",
            "ja-jp": "ja",
            "ko": "ko",
            "ko-kr": "ko",
            "vi": "vi",
            "vi-vn": "vi",
            "km": "km",
            "km-kh": "km",
            "th": "th",
            "th-th": "th",
            "ar": "ar",
            "ar-sa": "ar",
            "ru": "ru",
            "ru-ru": "ru",
            "zh": "zh",
            "zh-cn": "zh",
            "zh-tw": "zh",
        }
        if normalized in mapping:
            return mapping[normalized]
        base = normalized.split("-")[0]
        return mapping.get(base, base)

    @staticmethod
    def _language_map() -> dict[str, str]:
        return {
            "en": "eng_Latn",
            "fr": "fra_Latn",
            "es": "spa_Latn",
            "de": "deu_Latn",
            "it": "ita_Latn",
            "pt": "por_Latn",
            "ja": "jpn_Jpan",
            "ko": "kor_Hang",
            "vi": "vie_Latn",
            "km": "khm_Khmr",
            "th": "tha_Thai",
            "ar": "arb_Arab",
            "ru": "rus_Cyrl",
            "zh": "zho_Hans",
        }

    @classmethod
    def _ensure_dependencies(cls) -> None:
        try:
            import torch  # noqa: F401
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # noqa: F401
        except Exception as exc:  # pragma: no cover - environment fault path
            raise TranslationConfigurationError(
                "Local translation requires the 'transformers', 'torch', and 'sentencepiece' packages. "
                "Install them with: pip install transformers torch sentencepiece"
            ) from exc

    @classmethod
    def _get_model_bundle(cls) -> tuple[Any, Any]:
        cls._ensure_dependencies()
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        if cls._tokenizer is None or cls._model is None:
            with cls._lock:
                if cls._tokenizer is None or cls._model is None:
                    model_name = (TRANSLATION_LOCAL_MODEL or "facebook/nllb-200-distilled-600M").strip()
                    cls._tokenizer = AutoTokenizer.from_pretrained(model_name)
                    cls._model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
                    cls._model.eval()
        return cls._tokenizer, cls._model

    def supports_language_pair(self, source: str, target: str) -> bool:
        source_code = self.normalize_language_code(source, default="en")
        target_code = self.normalize_language_code(target, default="en")
        return source_code in self._language_map() and target_code in self._language_map()

    def translate(self, text: str, source: str, target: str) -> str:
        if text is None:
            return ""
        clean_text = str(text).strip()
        if not clean_text:
            return ""

        source_lang = self.normalize_language_code(source, default="en")
        target_lang = self.normalize_language_code(target, default="en")
        if not self.supports_language_pair(source_lang, target_lang):
            raise TranslationUnsupportedLanguageError(
                f"Local model does not support {source_lang} -> {target_lang}."
            )

        tokenizer, model = self._get_model_bundle()
        source_code = self._language_map().get(source_lang, source_lang)
        target_code = self._language_map().get(target_lang, target_lang)

        try:
            if hasattr(tokenizer, "src_lang") and hasattr(tokenizer, "tgt_lang"):
                inputs = tokenizer(clean_text, return_tensors="pt", src_lang=source_code)
            else:
                inputs = tokenizer(clean_text, return_tensors="pt")

            device = next(model.parameters()).device
            inputs = {key: value.to(device) for key, value in inputs.items()}
            if hasattr(tokenizer, "lang_code_to_id") and target_code in tokenizer.lang_code_to_id:
                forced_bos = tokenizer.lang_code_to_id[target_code]
                generated = model.generate(**inputs, forced_bos_token_id=forced_bos)
            else:
                generated = model.generate(**inputs)
            translated = tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
            translated = str(translated).strip()
            if not translated:
                raise TranslationError("Local translation produced an empty result.")
            return translated
        except Exception as exc:  # pragma: no cover - model runtime path
            raise TranslationError(f"Local translation failed for {source_lang} -> {target_lang}: {exc}") from exc


class GoogleProvider:
    name = "google"

    def __init__(self) -> None:
        if GoogleTranslator is None:
            raise TranslationConfigurationError(
                "The optional Google fallback requires 'deep-translator'. Install it with: pip install deep-translator"
            )

    def translate(self, text: str, source: str, target: str) -> str:
        source_lang = LocalTranslationProvider.normalize_language_code(source, default="en")
        target_lang = LocalTranslationProvider.normalize_language_code(target, default="en")
        translator = GoogleTranslator(source=source_lang, target=target_lang)
        result = translator.translate(text)
        translated = str(result).strip()
        if not translated:
            raise TranslationError("Google translator returned an empty payload.")
        return translated


class MyMemoryProvider:
    name = "mymemory"

    def __init__(self) -> None:
        if MyMemoryTranslator is None:
            raise TranslationConfigurationError(
                "The optional MyMemory fallback requires 'deep-translator'. Install it with: pip install deep-translator"
            )

    @staticmethod
    def _language_name(lang: str) -> str:
        mapping = {
            "en": "english",
            "fr": "french",
            "es": "spanish",
            "de": "german",
            "it": "italian",
            "pt": "portuguese",
            "ja": "japanese",
            "ko": "korean",
            "vi": "vietnamese",
            "km": "khmer",
            "th": "thai",
            "ar": "arabic",
            "ru": "russian",
            "zh": "chinese",
        }
        return mapping.get(lang, lang)

    def translate(self, text: str, source: str, target: str) -> str:
        source_lang = LocalTranslationProvider.normalize_language_code(source, default="en")
        target_lang = LocalTranslationProvider.normalize_language_code(target, default="en")
        translator = MyMemoryTranslator(
            source=self._language_name(source_lang),
            target=self._language_name(target_lang),
        )
        result = translator.translate(text)
        translated = str(result).strip()
        if not translated:
            raise TranslationError("MyMemory translator returned an empty payload.")
        return translated


class TranslationManager:
    def __init__(self, *, sleep=None, clock=None, jitter=None) -> None:
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        self.primary_provider = (TRANSLATION_PROVIDER or "local").strip().lower() or "local"
        self.fallback_provider = (TRANSLATION_FALLBACK_PROVIDER or "google").strip().lower() or "google"
        self.offline_mode = bool(TRANSLATION_OFFLINE)
        self.max_retries = max(1, int(TRANSLATION_MAX_RETRIES))
        self.min_delay = max(0.1, float(TRANSLATION_MIN_DELAY))
        self.backoff_base = max(1.0, float(TRANSLATION_BACKOFF_BASE))
        self.timeout = max(1.0, float(TRANSLATION_TIMEOUT))
        self.cache = TranslationCache()
        self.local_provider = LocalTranslationProvider(model_name=TRANSLATION_LOCAL_MODEL)
        self.online_providers: dict[str, Any] = {}
        if not self.offline_mode:
            for name in ["google", "mymemory"]:
                try:
                    if name == "google":
                        self.online_providers[name] = GoogleProvider()
                    else:
                        self.online_providers[name] = MyMemoryProvider()
                except TranslationConfigurationError as exc:
                    logger.warning("Skipping optional provider %s: %s", name, exc)

    @staticmethod
    def normalize_language_code(value: str | None, *, default: str = "en") -> str:
        return LocalTranslationProvider.normalize_language_code(value, default=default)

    @staticmethod
    def normalize_target_language(value: str | None) -> str:
        return TranslationManager.normalize_language_code(value, default="en")

    def _provider_priority(self) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        if self.offline_mode:
            return ["local"]

        for provider_name in [self.primary_provider, self.fallback_provider]:
            provider_name = (provider_name or "").strip().lower()
            if not provider_name or provider_name in seen:
                continue
            if provider_name in {"local", "google", "mymemory"}:
                ordered.append(provider_name)
                seen.add(provider_name)

        if not ordered:
            ordered = ["local"]
        return ordered

    def _calculate_backoff(self, attempt: int, exc: Exception | None = None) -> float:
        retry_after = self._extract_retry_after(exc)
        if retry_after is not None:
            return max(retry_after, self.min_delay) + (self._jitter or random.uniform)(0.0, 0.25)
        return self.min_delay * (self.backoff_base ** max(0, attempt - 1)) + (self._jitter or random.uniform)(0.0, 0.5)

    @staticmethod
    def _extract_retry_after(exc: Exception | None) -> float | None:
        if exc is None:
            return None
        response = getattr(exc, "response", None)
        if response is None:
            return None
        headers = getattr(response, "headers", {})
        retry_after = headers.get("Retry-After") if isinstance(headers, dict) else None
        if retry_after is None:
            return None
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        if isinstance(exc, (TranslationUnsupportedLanguageError, TranslationOfflineModeError, TranslationConfigurationError)):
            return False
        if isinstance(exc, (TranslationRateLimitError, TranslationError)):
            return True
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if status in (429, 500, 502, 503, 504):
            return True
        return str(exc).strip().lower() in {"429", "500", "502", "503", "504"}

    def _call_provider(self, provider_name: str, source: str, target: str, text: str) -> str:
        provider_name = (provider_name or "local").strip().lower()
        source_lang = self.normalize_language_code(source, default="en")
        target_lang = self.normalize_target_language(target)

        if provider_name == "local":
            return self.local_provider.translate(text, source_lang, target_lang)

        if self.offline_mode:
            raise TranslationOfflineModeError("Offline mode is enabled; external translation providers are disabled.")

        if provider_name not in self.online_providers:
            raise TranslationUnsupportedLanguageError(f"Unsupported provider: {provider_name}")

        provider = self.online_providers[provider_name]
        try:
            return provider.translate(text, source_lang, target_lang)
        except Exception as exc:
            if isinstance(exc, (TooManyRequests, RequestError)):
                raise TranslationRateLimitError(f"Rate limited by {provider_name}: {exc}") from exc
            raise TranslationError(f"{provider_name.title()} translation failed: {exc}") from exc

    def _translate_with_retry(self, provider_name: str, source: str, target: str, text: str) -> str:
        if provider_name == "local":
            return self._call_provider(provider_name, source, target, text)

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return self._call_provider(provider_name, source, target, text)
            except Exception as exc:
                if not self._is_retryable_error(exc):
                    raise
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                delay = self._calculate_backoff(attempt, exc)
                logger.warning("Transient failure from %s. Retrying in %.2fs (attempt %s/%s)", provider_name, delay, attempt, self.max_retries)
                clock = self._clock or time.monotonic
                started = clock()
                (self._sleep or time.sleep)(delay)
                logger.debug("Retry wait completed in %.3fs", max(0.0, clock() - started))

        if last_error is not None:
            raise TranslationError(f"Translation failed for {provider_name}: {last_error}")
        raise TranslationError(f"Translation failed for {provider_name} after {self.max_retries} attempts.")

    def translate_text(self, text: str, source: str = "en", target: str = "fr") -> str:
        if text is None:
            return ""
        clean_text = str(text).strip()
        if not clean_text:
            return ""

        source_lang = self.normalize_language_code(source, default="en")
        target_lang = self.normalize_target_language(target)

        cached = self.cache.get(source_lang, target_lang, clean_text)
        if cached is not None:
            return cached

        last_error: Exception | None = None
        for provider_name in self._provider_priority():
            try:
                translated = self._translate_with_retry(provider_name, source_lang, target_lang, clean_text)
                self.cache.set(source_lang, target_lang, clean_text, translated)
                return translated
            except (TranslationError, TranslationConfigurationError, TranslationOfflineModeError, TranslationUnsupportedLanguageError) as exc:
                last_error = exc
                logger.warning("Translation provider %s failed for %s -> %s: %s", provider_name, source_lang, target_lang, exc)
                if self.offline_mode:
                    break
                continue

        if last_error is not None:
            raise TranslationError(f"All translation providers failed for {source_lang} -> {target_lang}: {last_error}")
        raise TranslationError(f"No translation providers are configured for {source_lang} -> {target_lang}.")

    def translate_batch(self, texts: list[str], source: str = "en", target: str = "fr") -> list[str]:
        if not texts:
            return []

        normalized_source = self.normalize_language_code(source, default="en")
        normalized_target = self.normalize_target_language(target)
        unique_order: list[str] = []
        seen: set[str] = set()
        for item in texts:
            clean = str(item).strip()
            if not clean:
                continue
            if clean in seen:
                continue
            seen.add(clean)
            unique_order.append(clean)

        translated_map: dict[str, str] = {}
        for unique_text in unique_order:
            translated_map[unique_text] = self.translate_text(unique_text, source=normalized_source, target=normalized_target)

        result: list[str] = []
        for item in texts:
            clean = str(item).strip()
            if not clean:
                result.append("")
                continue
            result.append(translated_map.get(clean, clean))
        return result


def normalize_target_language(value: str | None) -> str:
    return TranslationManager.normalize_target_language(value)


def translate_text(text: str, source: str = "en", target: str = "fr") -> str:
    return TranslationManager().translate_text(text, source=source, target=target)
