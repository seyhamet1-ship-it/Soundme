from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.audio_quality import SpeechQualityError, require_real_speech
from app.providers import DemoToneTTS, synthesize_khmer, translate_khmer


class ProviderReliabilityTests(unittest.TestCase):
    def test_nllb_failure_does_not_silently_use_demo_translation(self):
        with patch.dict(os.environ, {"TRANSLATION_PROVIDER": "nllb", "DEMO_MODE": ""}):
            with patch("app.providers.NllbTranslator", side_effect=RuntimeError("model unavailable")):
                with self.assertRaisesRegex(RuntimeError, "no fixture fallback"):
                    translate_khmer("hello", "en")

    def test_demo_translation_requires_explicit_demo_mode(self):
        with patch.dict(os.environ, {"TRANSLATION_PROVIDER": "demo", "DEMO_MODE": ""}):
            with self.assertRaisesRegex(RuntimeError, "requires DEMO_MODE=1"):
                translate_khmer("hello", "en")

    def test_explicit_demo_translation_is_provenanced(self):
        with patch.dict(os.environ, {"TRANSLATION_PROVIDER": "demo", "DEMO_MODE": "1"}):
            text, provider = translate_khmer("hello everyone, welcome to our channel.", "en")
        self.assertTrue(text)
        self.assertEqual(provider, "demo-fixture")

    def test_edge_failure_does_not_create_tone_fallback(self):
        class FailedEdge:
            async def synthesize(self, *args, **kwargs):
                raise RuntimeError("provider unavailable")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"
            with patch.dict(os.environ, {"DEMO_MODE": "", "TTS_PROVIDER": "edge"}):
                with patch("app.providers.EdgeTTSProvider", FailedEdge):
                    with self.assertRaisesRegex(RuntimeError, "TTS_UNAVAILABLE"):
                        synthesize_khmer("សួស្តី", "km-KH-SreymomNeural", output, 1)
            self.assertFalse(output.exists())

    def test_auto_fallback_edge_to_mms_succeeds(self):
        calls = []

        class FailedEdge:
            async def synthesize(self, *args, **kwargs):
                calls.append("edge")
                raise RuntimeError("edge down")

        class OkMMS:
            def synthesize(self, text, destination):
                calls.append("mms")
                destination.write_bytes(b"RIFF0000WAVE")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"
            with patch.dict(os.environ, {"DEMO_MODE": "", "TTS_PROVIDER": "auto", "AZURE_SPEECH_KEY": "", "AZURE_SPEECH_REGION": ""}):
                with patch("app.providers.EdgeTTSProvider", FailedEdge):
                    with patch("app.providers.MMSTTSProvider", OkMMS):
                        provider = synthesize_khmer("សួស្តី", "km-KH-SreymomNeural", output, 1)
            self.assertEqual(calls, ["edge", "mms"])
            self.assertEqual(provider, "mms-facebook/mms-tts-khm")
            self.assertTrue(output.exists())

    def test_auto_fallback_edge_to_azure_when_configured(self):
        calls = []

        class FailedEdge:
            async def synthesize(self, *args, **kwargs):
                calls.append("edge")
                raise RuntimeError("edge down")

        class OkAzure:
            def synthesize(self, text, voice, destination, **kwargs):
                calls.append("azure")
                destination.write_bytes(b"RIFF0000WAVE")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"
            with patch.dict(
                os.environ,
                {
                    "DEMO_MODE": "",
                    "TTS_PROVIDER": "auto",
                    "TTS_PROVIDER_ORDER": "edge,azure",
                    "AZURE_SPEECH_KEY": "test-key",
                    "AZURE_SPEECH_REGION": "eastus",
                },
            ):
                with patch("app.providers.EdgeTTSProvider", FailedEdge):
                    with patch("app.providers.AzureTTSProvider", OkAzure):
                        provider = synthesize_khmer("សួស្តី", "km-KH-SreymomNeural", output, 1)
            self.assertEqual(calls, ["edge", "azure"])
            self.assertEqual(provider, "azure-speech")

    def test_all_providers_failing_raises_tts_unavailable(self):
        class FailedEdge:
            async def synthesize(self, *args, **kwargs):
                raise RuntimeError("edge down")

        class FailedMMS:
            def synthesize(self, text, destination):
                raise RuntimeError("mms down")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"
            with patch.dict(os.environ, {"DEMO_MODE": "", "TTS_PROVIDER": "auto", "AZURE_SPEECH_KEY": "", "AZURE_SPEECH_REGION": ""}):
                with patch("app.providers.EdgeTTSProvider", FailedEdge):
                    with patch("app.providers.MMSTTSProvider", FailedMMS):
                        with self.assertRaisesRegex(RuntimeError, "TTS_UNAVAILABLE"):
                            synthesize_khmer("សួស្តី", "km-KH-SreymomNeural", output, 1)
            self.assertFalse(output.exists())

    def test_disabled_provider_raises_tts_unavailable_without_side_effects(self):
        from app.providers import tts_provider_health

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"
            with patch.dict(os.environ, {"DEMO_MODE": "", "TTS_PROVIDER": "none"}):
                with self.assertRaisesRegex(RuntimeError, "TTS_UNAVAILABLE"):
                    synthesize_khmer("សួស្តី", "km-KH-SreymomNeural", output, 1)
                health = tts_provider_health()
            self.assertFalse(output.exists())
            self.assertFalse(health["edge-tts"])

    def test_demo_tone_is_marked_and_rejected_by_speech_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "tone.wav"
            with patch.dict(os.environ, {"DEMO_MODE": "1", "TTS_PROVIDER": "demo"}):
                provider = synthesize_khmer("test", "demo", output, 1)
            self.assertEqual(provider, "demo-tone-not-speech")
            with self.assertRaises(SpeechQualityError):
                require_real_speech(output)

    def test_khmer_gate_allows_latin_brand_but_rejects_english(self):
        from app.segments import is_khmer_text

        self.assertTrue(is_khmer_text("អរគុណអ្នក ដែលបានទទួលស្វាគមន៍ Soundme នេះជាការសាកល្បង។"))
        self.assertTrue(is_khmer_text("សួស្តីអ្នកទាំងអស់គ្នា"))
        self.assertFalse(is_khmer_text("Hello everyone, welcome to our channel."))
        self.assertFalse(is_khmer_text(""))

    def test_guard_rejects_silence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "silence.wav"
            DemoToneTTS().synthesize(output, 1)
            # Replace the fixture data with valid-format silence.
            import wave
            with wave.open(str(output), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(bytes(24000 * 2))
            with self.assertRaises(SpeechQualityError):
                require_real_speech(output)


if __name__ == "__main__":
    unittest.main()
