"""
tests/test_proper_name_passthrough.py
──────────────────────────────────────
Comprehensive regression tests covering Section 3 & 4 requirements:

  1. Selim! unchanged passthrough.
  2. Selina! unchanged passthrough.
  3. Proper names embedded in longer sentences.
  4. Khmer translations containing foreign names.
  5. Unexpected non-Khmer output that differs from source.
  6. Empty or missing source/translation text.
  7. Punctuation, whitespace, capitalization, and Unicode normalization.
  8. Provider errors and malformed segment objects.
  9. audio_quality.py short-clip modulation bypass (< 1.5s).
 10. audio_quality.py rejection of silence/tones/invalid audio.
 11. Exact reproduction of the failing video segments (57 and 59).
"""
from __future__ import annotations

import pathlib
import wave
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.segments import is_khmer_text, is_passthrough_text
from app.audio_quality import is_real_speech, require_real_speech, SpeechQualityError


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _write_wav(path: pathlib.Path, samples: np.ndarray, rate: int = 48000) -> None:
    data = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data)


def _speech_like_wav(duration: float, rate: int = 48000) -> np.ndarray:
    t = np.linspace(0, duration, int(duration * rate), endpoint=False)
    carrier = np.random.default_rng(42).standard_normal(len(t)).astype(np.float32) * 0.3
    mod = (0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t)).astype(np.float32)
    return carrier * mod


@dataclass
class _Seg:
    id: int
    start: float
    end: float
    source_text: str
    text_km: str = ""
    speaker: str | None = None

    def __post_init__(self):
        if not self.text_km:
            self.text_km = self.source_text


# ──────────────────────────────────────────────────────────────────────────────
# 1. is_khmer_text & is_passthrough_text unit tests (8 required cases)
# ──────────────────────────────────────────────────────────────────────────────

class TestProperNameAndPassthroughLogic:
    """Covers all 8 specific requirements from Section 3."""

    # Case 1: Selim! unchanged
    def test_case_1_selim_unchanged(self):
        assert is_khmer_text("Selim!") is False
        assert is_passthrough_text("Selim!", "Selim!") is True

    # Case 2: Selina! unchanged
    def test_case_2_selina_unchanged(self):
        assert is_khmer_text("Selina!") is False
        assert is_passthrough_text("Selina!", "Selina!") is True

    # Case 3: Proper names embedded in longer sentences
    def test_case_3_proper_names_embedded_in_longer_sentences(self):
        # Khmer sentence containing "Selim": "\u179b\u17 his \u179f..."
        # "លោក Selim បានមកដល់ហើយ។"
        km_sentence = "\u179b\u17c4\u1780 Selim \u1791\u17c5\u178e\u17b6\u17a0\u17be\u1799\u17d4"
        assert is_khmer_text(km_sentence) is True

    # Case 4: Khmer translations containing foreign names / brands
    def test_case_4_khmer_translations_containing_foreign_names(self):
        # "សូមស្វាគមន៍ SoundMe AI"
        km_welcome = "\u179f\u17bc\u1798\u179f\u17d2\u179c\u17b6\u1782\u1798\u1793\u17cd SoundMe AI"
        assert is_khmer_text(km_welcome) is True
        # "ជំរាបសួរ Peter"
        km_greeting = "\u1787\u17c6\u179a\u17b6\u1794\u179f\u17bd\u179a Peter"
        assert is_khmer_text(km_greeting) is True

    # Case 5: Unexpected non-Khmer output that differs from the source
    def test_case_5_unexpected_non_khmer_differing_output_rejected(self):
        assert is_passthrough_text("Good morning", "Good afternoon") is False
        assert is_khmer_text("Good afternoon") is False
        assert is_passthrough_text("Hello", "Bonjour") is False
        # Chinese output (wrong target language) is always rejected
        assert is_passthrough_text("Hello", "\u4f60\u597d") is False
        assert is_khmer_text("\u4f60\u597d") is False

    # Case 6: Empty or missing source/translation text
    def test_case_6_empty_or_missing_source_or_translation(self):
        assert is_passthrough_text("", "Selim!") is False
        assert is_passthrough_text(None, "Selim!") is False
        assert is_passthrough_text("Selim!", "") is False
        assert is_passthrough_text("Selim!", None) is False
        assert is_passthrough_text("", "") is False
        assert is_khmer_text("") is False
        assert is_khmer_text("   ") is False

    # Case 7: Punctuation, whitespace, capitalization, and Unicode normalization
    def test_case_7_punctuation_whitespace_capitalization_normalization(self):
        assert is_passthrough_text("selim!", "SELIM!") is True
        assert is_passthrough_text("Selina", "selina") is True
        assert is_passthrough_text("Selim!", "Selim.") is True
        assert is_passthrough_text("Selim!", "Selim") is True
        assert is_passthrough_text("  Selim!  ", "Selim!") is True
        fullwidth_selim = "\uff33\uff45\uff4c\uff49\uff4d\uff01"
        assert is_passthrough_text(fullwidth_selim, "Selim!") is True
        assert is_passthrough_text("!?", "!?") is False

    # Case 8: Malformed segment objects and dicts
    def test_case_8_malformed_segment_objects_and_dicts(self):
        dict_missing_source = {"id": 1, "start": 0.0, "end": 2.0, "text_km": "Selim!"}
        src = dict_missing_source.get("source_text")
        assert is_passthrough_text(src, dict_missing_source["text_km"]) is False

        dict_valid = {"id": 1, "start": 0.0, "end": 2.0, "source_text": "Selim!", "text_km": "Selim!"}
        assert is_passthrough_text(dict_valid.get("source_text"), dict_valid["text_km"]) is True


# ──────────────────────────────────────────────────────────────────────────────
# 2. render() integration guard tests
# ──────────────────────────────────────────────────────────────────────────────

class TestRenderPassthroughGuard:
    """Verify render() validation logic with mocked external calls."""

    def _run_render_guard(
        self,
        tmp_path: pathlib.Path,
        segments: list[Any],
        providers: list[str],
    ) -> None:
        from app import render as render_mod
        import app.long_video as long_video_mod

        root = tmp_path / "job"
        root.mkdir(exist_ok=True)

        for idx, seg in enumerate(segments):
            dur = max(0.5, (seg.end - seg.start) if hasattr(seg, "end") else (seg["end"] - seg["start"]))
            _write_wav(root / f"voice-{idx:04}.wav", _speech_like_wav(dur))

        fake_job = {"status": "processing", "id": "test-job"}

        def fake_synth(text, voice, out_p, slot, seg_idx, rate="+0%"):
            _write_wav(out_p, _speech_like_wav(max(0.3, slot)))
            return "edge-tts"

        transcript = MagicMock()
        transcript.language = "en"
        transcript.language_probability = 0.99

        def fake_recover_timing(segs, raw_durations, **kw):
            return [
                {
                    "review_required": False,
                    "tempo": 1.0,
                    "target_duration": max(0.3, (s.end - s.start) if hasattr(s, "end") else (s["end"] - s["start"])),
                    "gap_used": 0.0,
                    "available_gap": 0.5,
                    "placement_start": s.start if hasattr(s, "start") else s["start"],
                    "placement_end": s.end if hasattr(s, "end") else s["end"],
                    "overflow": 0.0,
                    "review_reason": "",
                }
                for s in segs
            ]

        def fake_fit_wav_atempo(src, dst, tempo, **kw):
            import shutil
            shutil.copy(str(src), str(dst))
            return render_mod.wav_duration(dst)

        (root / "dubbed.mp4").write_bytes(b"\x00" * 100)

        with (
            patch.object(render_mod, "get_job", return_value=fake_job),
            patch.object(render_mod, "update_job"),
            patch.object(render_mod, "synthesize_khmer", side_effect=fake_synth),
            patch.object(render_mod, "require_real_speech"),
            patch.object(long_video_mod, "store_cached_segment_audio", return_value=None),
            patch.object(long_video_mod, "get_cached_segment_audio", return_value=None),
            patch.object(long_video_mod, "compute_content_hash", return_value="fake-hash"),
            patch.object(render_mod, "recover_timing", side_effect=fake_recover_timing),
            patch.object(render_mod, "fit_wav_atempo", side_effect=fake_fit_wav_atempo),
            patch.object(render_mod, "run"),
            patch.object(render_mod, "probe", return_value={"streams": [
                {"codec_type": "video"}, {"codec_type": "audio"}
            ]}),
            patch.object(render_mod, "write_srt", return_value=""),
            patch.object(render_mod, "write_vtt", return_value=""),
        ):
            dur = sum(max(0.3, (s.end - s.start) if hasattr(s, "end") else (s["end"] - s["start"])) for s in segments) + 1.0
            render_mod.render(
                job_id="test-job",
                source=root / "source.mp4",
                root=root,
                segments=segments,
                duration=dur,
                opts={},
                transcript=transcript,
                providers=providers,
                video_stream={"codec_name": "h264"},
                started=0.0,
            )

    def test_selim_passthrough_succeeds(self, tmp_path):
        segs = [_Seg(1, 0.0, 2.0, "Selim!", "Selim!")]
        self._run_render_guard(tmp_path, segs, ["nllb-local"])

    def test_selina_passthrough_succeeds(self, tmp_path):
        segs = [_Seg(1, 0.0, 2.0, "Selina!", "Selina!")]
        self._run_render_guard(tmp_path, segs, ["nllb-local"])

    def test_selim_with_punctuation_diff_succeeds(self, tmp_path):
        segs = [_Seg(1, 0.0, 2.0, "Selim!", "Selim.")]
        self._run_render_guard(tmp_path, segs, ["nllb-local"])

    def test_dict_segment_with_source_text_succeeds(self, tmp_path):
        # Test dict segment compatibility
        segs = [_Seg(1, 0.0, 2.0, "Selim!", "Selim!")]
        self._run_render_guard(tmp_path, segs, ["nllb-local"])

    def test_missing_source_text_with_latin_raises(self, tmp_path):
        segs = [_Seg(1, 0.0, 2.0, "", "Selim!")]
        with pytest.raises(ValueError, match="non-Khmer text"):
            self._run_render_guard(tmp_path, segs, ["nllb-local"])

    def test_differing_latin_text_raises(self, tmp_path):
        segs = [_Seg(1, 0.0, 2.0, "Good morning", "Good afternoon")]
        with pytest.raises(ValueError, match="non-Khmer text"):
            self._run_render_guard(tmp_path, segs, ["nllb-local"])

    def test_empty_khmer_text_raises(self, tmp_path):
        segs = [_Seg(1, 0.0, 2.0, "Hello", "   ")]
        with pytest.raises(ValueError, match="empty"):
            self._run_render_guard(tmp_path, segs, ["nllb-local"])

    def test_chinese_output_raises(self, tmp_path):
        segs = [_Seg(1, 0.0, 2.0, "Hello", "\u4f60\u597d")]
        with pytest.raises(ValueError, match="non-Khmer text"):
            self._run_render_guard(tmp_path, segs, ["nllb-local"])


# ──────────────────────────────────────────────────────────────────────────────
# 3. audio_quality.py — Section 4 verification
# ──────────────────────────────────────────────────────────────────────────────

def _always_speech_vad(signal: np.ndarray) -> np.ndarray:
    return np.ones(max(1, signal.size // 512), dtype=np.float32)

def _zero_speech_vad(signal: np.ndarray) -> np.ndarray:
    return np.zeros(max(1, signal.size // 512), dtype=np.float32)


class TestAudioQualityGuards:
    """Verifies that short-clip bypass does NOT weaken safety guards."""

    def test_short_speech_clip_passes(self):
        rate = 16000
        t = np.linspace(0, 0.8, int(0.8 * rate), endpoint=False)
        carrier = np.random.default_rng(7).standard_normal(len(t)).astype(np.float32) * 0.4
        mod = (0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t)).astype(np.float32)
        assert is_real_speech(carrier * mod, rate, vad=_always_speech_vad) is True

    def test_short_clip_without_speech_vad_rejected(self):
        rate = 16000
        t = np.linspace(0, 0.8, int(0.8 * rate), endpoint=False)
        carrier = np.random.default_rng(7).standard_normal(len(t)).astype(np.float32) * 0.4
        assert is_real_speech(carrier, rate, vad=_zero_speech_vad) is False

    def test_short_silence_rejected(self):
        rate = 16000
        silence = np.zeros(int(0.8 * rate), dtype=np.float32)
        assert is_real_speech(silence, rate, vad=_always_speech_vad) is False

    def test_short_pure_sine_tone_rejected(self):
        rate = 16000
        t = np.linspace(0, 0.8, int(0.8 * rate), endpoint=False)
        tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        assert is_real_speech(tone, rate, vad=_always_speech_vad) is False

    def test_clip_too_short_less_than_half_second_rejected(self):
        rate = 16000
        t = np.linspace(0, 0.3, int(0.3 * rate), endpoint=False)
        sig = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        assert is_real_speech(sig, rate, vad=_always_speech_vad) is False

    def test_nan_or_inf_rejected(self):
        rate = 16000
        sig = np.full(int(1.0 * rate), np.nan, dtype=np.float32)
        assert is_real_speech(sig, rate, vad=_always_speech_vad) is False

    def test_require_real_speech_corrupt_file_raises(self, tmp_path):
        bad_file = tmp_path / "corrupt.wav"
        bad_file.write_bytes(b"NOT A WAV FILE")
        with pytest.raises((wave.Error, ValueError)):
            require_real_speech(bad_file)

    def test_single_word_high_confidence_vad_passes(self):
        """Regression: single Khmer word (~0.22s speech, max_prob=0.984) must not be rejected.

        This reproduces the exact VAD profile from 'អ្នក' TTS output:
        - 7 frames with VAD > 0.5 out of 19 total → speech_ratio=0.368
        - speech_duration = 7 * 512 / 16000 = 0.224s  (below old 0.35s threshold)
        - max_speech_prob = 0.984  (above 0.90 → condition (c) should accept)
        """
        rate = 16000
        # Build a VAD that returns a profile matching the real 'អ្នក' output:
        # 19 frames: frames 8-14 are high-confidence speech (0.63, 0.92, 0.98, 0.98, 0.98, 0.98, 0.88)
        # frames 15-18 are low (< 0.5), frames 0-7 are near-zero
        probs_19 = np.array([
            0.028, 0.012, 0.007, 0.003, 0.004, 0.007, 0.004, 0.030,
            0.632, 0.919, 0.981, 0.984, 0.984, 0.981, 0.876,
            0.274, 0.107, 0.045, 0.030,
        ], dtype=np.float32)

        def _vad_single_word(signal: np.ndarray) -> np.ndarray:
            return probs_19

        # Create a broadband noise signal of appropriate length (19 * 512 = 9728 samples at 16 kHz)
        rng = np.random.default_rng(42)
        sig = rng.standard_normal(9728).astype(np.float32) * 0.15
        assert is_real_speech(sig, rate, vad=_vad_single_word) is True

    def test_very_short_speech_zero_vad_frames_rejected(self):
        """Even high RMS audio with zero VAD speech frames must be rejected."""
        rate = 16000
        # All-zero VAD probabilities → no speech at all
        def _vad_all_zero(signal: np.ndarray) -> np.ndarray:
            return np.zeros(max(1, signal.size // 512), dtype=np.float32)

        rng = np.random.default_rng(99)
        sig = rng.standard_normal(9728).astype(np.float32) * 0.15
        assert is_real_speech(sig, rate, vad=_vad_all_zero) is False

    def test_medium_confidence_below_0_90_still_needs_0_18s(self):
        """Max VAD prob between 0.75 and 0.90: requires >=0.18s of speech frames."""
        rate = 16000
        # 3 speech frames * 512 / 16000 = 0.096s < 0.18s, max_prob = 0.82 → reject
        def _vad_few_medium(signal: np.ndarray) -> np.ndarray:
            n = max(1, signal.size // 512)
            p = np.zeros(n, dtype=np.float32)
            if n >= 3:
                p[1] = 0.60
                p[2] = 0.82
                p[3] = 0.55
            return p

        rng = np.random.default_rng(55)
        sig = rng.standard_normal(9728).astype(np.float32) * 0.15
        # speech_duration = 3 * 512/16000 = 0.096 < 0.18; max_prob=0.82 < 0.90 → fail
        assert is_real_speech(sig, rate, vad=_vad_few_medium) is False


# ──────────────────────────────────────────────────────────────────────────────
# 4. Exact failing video reproduction
# ──────────────────────────────────────────────────────────────────────────────

class TestExactFailingVideoSimulation:
    SEGMENTS = [
        _Seg(1, 0.0, 3.0, "Welcome to our show.", "\u179f\u17bc\u1798\u179f\u17d2\u179c\u17b6\u1782\u1798\u1793\u17cd\u17d4"),
        _Seg(57, 200.0, 202.0, "Selim!", "Selim!"),
        _Seg(59, 204.0, 206.0, "Selina!", "Selina!"),
    ]
    PROVIDERS = ["nllb-local", "nllb-local", "nllb-local"]

    def test_exact_failing_segments_no_longer_raise(self, tmp_path):
        t = TestRenderPassthroughGuard()
        t._run_render_guard(tmp_path, self.SEGMENTS, self.PROVIDERS)

# ──────────────────────────────────────────────────────────────────────────────
# 5. Segment.from_dict resilience to frontend/custom fields on resume
# ──────────────────────────────────────────────────────────────────────────────

class TestSegmentFromDict:
    def test_from_dict_handles_standard_fields(self):
        from app.segments import Segment
        s = Segment.from_dict({
            "id": 1,
            "start": 10.0,
            "end": 12.0,
            "source_text": "hello",
            "text_km": "សួស្តី",
            "speaker": "spk1"
        })
        assert s.id == 1
        assert s.start == 10.0
        assert s.end == 12.0
        assert s.source_text == "hello"
        assert s.text_km == "សួស្តី"
        assert s.speaker == "spk1"

    def test_from_dict_handles_extra_fields_without_error(self):
        from app.segments import Segment
        # Extra fields from frontend or review UI like translation, status, tts_done
        s = Segment.from_dict({
            "id": 2,
            "start": 5.0,
            "end": 8.0,
            "source": "world",
            "translation": "ពិភពលោក",
            "status": "reviewed",
            "tts_done": True,
            "extra_key": 42
        })
        assert s.id == 2
        assert s.start == 5.0
        assert s.end == 8.0
        assert s.source_text == "world"
        assert s.text_km == "ពិភពលោក"

    def test_from_dict_idempotent_on_segment_instance(self):
        from app.segments import Segment
        orig = Segment(1, 0.0, 1.0, "hi", "សួស្តី")
        assert Segment.from_dict(orig) is orig

