import unittest
from unittest.mock import patch

from app.services.translation import TranslationError, TranslationManager, TranslationRateLimitError


class TranslationServiceTests(unittest.TestCase):
    def test_translate_text_uses_cache(self):
        manager = TranslationManager()
        manager.primary_provider = "local"
        manager.fallback_provider = "google"

        with patch.object(manager, "_translate_with_retry", return_value="Bonjour") as mock_retry:
            first = manager.translate_text("hello", source="en", target="fr")
            second = manager.translate_text("hello", source="en", target="fr")
            self.assertEqual(first, "Bonjour")
            self.assertEqual(second, "Bonjour")
            self.assertEqual(mock_retry.call_count, 1)

    def test_429_retries_with_backoff(self):
        manager = TranslationManager()
        manager.primary_provider = "google"
        manager.offline_mode = False
        manager.max_retries = 3
        manager.min_delay = 0.01
        manager.backoff_base = 2
        manager._jitter = lambda low, high: 0

        with patch.object(manager, "_call_provider", side_effect=[Exception("429"), "Bonjour"]) as mock_call:
            with patch("app.services.translation.time.sleep") as mock_sleep:
                result = manager._translate_with_retry("google", "en", "fr", "hello")
                self.assertEqual(result, "Bonjour")
                self.assertEqual(mock_call.call_count, 2)
                self.assertGreaterEqual(mock_sleep.call_count, 1)

    def test_unsupported_language_raises_translation_error(self):
        manager = TranslationManager()
        with patch.object(manager, "_translate_with_retry", side_effect=TranslationError("unsupported")):
            with self.assertRaises(TranslationError):
                manager.translate_text("hello", source="en", target="xx")

    def test_local_provider_english_khmer_translation(self):
        manager = TranslationManager()
        manager.primary_provider = "local"
        manager.offline_mode = True
        manager.local_provider.translate = lambda text, source, target: "សួស្តី" if text == "Hello, how are you?" else text

        result = manager.translate_text("Hello, how are you?", source="en", target="km")
        self.assertTrue(result)
        self.assertNotEqual(result, "Hello, how are you?")
        self.assertEqual(result, "សួស្តី")

    def test_offline_mode_prevents_online_calls(self):
        manager = TranslationManager()
        manager.offline_mode = True
        manager.primary_provider = "local"
        manager.local_provider.translate = lambda text, source, target: "សួស្តី"
        manager.online_providers["google"] = unittest.mock.Mock()

        result = manager.translate_text("Hello", source="en", target="km")
        self.assertEqual(result, "សួស្តី")
        manager.online_providers["google"].translate.assert_not_called()

    def test_translate_batch_deduplicates_and_preserves_order(self):
        manager = TranslationManager()
        manager.primary_provider = "local"
        manager.offline_mode = True
        manager.translate_text = lambda text, source="en", target="fr": {"Hello": "Bonjour", "Good morning": "Bonjour le matin"}.get(text, text)

        result = manager.translate_batch(["Hello", "Hello", "Good morning", "Hello"], source="en", target="fr")
        self.assertEqual(result, ["Bonjour", "Bonjour", "Bonjour le matin", "Bonjour"])


if __name__ == "__main__":
    unittest.main()
