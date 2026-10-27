from types import SimpleNamespace

import pytest

from app.providers import WhisperASR


def make_asr(responses):
    """Build a WhisperASR instance without loading a model or fixture media."""
    calls = []

    class Model:
        def transcribe(self, audio_input, *, language, vad_filter, beam_size):
            calls.append({"audio": audio_input, "language": language, "vad_filter": vad_filter, "beam_size": beam_size})
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            segments, info = response
            return iter(segments), info

    asr = object.__new__(WhisperASR)
    asr.model = Model()
    return asr, calls


@pytest.fixture(autouse=True)
def disable_batched(monkeypatch):
    monkeypatch.setenv("ASR_BATCHED", "0")
    monkeypatch.setattr(WhisperASR, "_load_wav", staticmethod(lambda _path: "synthetic-audio"))


def info(language="en", probability=.8):
    return SimpleNamespace(language=language, language_probability=probability)


def segment(text="recognized"):
    return SimpleNamespace(start=0, end=1, text=text)


def test_empty_vad_retries_once_without_vad_and_returns_segments():
    asr, calls = make_asr([([], info("en", .2)), ([segment("vocal content")], info("en", .9))])
    transcript = asr.transcribe("unused.wav")
    assert [call["vad_filter"] for call in calls] == [True, False]
    assert [item.text for item in transcript.segments] == ["vocal content"]
    assert transcript.language == "en"
    assert transcript.language_probability == .9


def test_vad_segments_do_not_trigger_fallback():
    asr, calls = make_asr([([segment()], info())])
    transcript = asr.transcribe("unused.wav", "en")
    assert len(calls) == 1 and calls[0]["vad_filter"] is True
    assert calls[0]["language"] == "en"
    assert len(transcript.segments) == 1


def test_both_attempts_empty_preserves_no_speech_result():
    asr, calls = make_asr([([], info("en", .2)), ([], info("en", .4))])
    transcript = asr.transcribe("unused.wav")
    assert [call["vad_filter"] for call in calls] == [True, False]
    assert transcript.segments == []
    assert transcript.language_probability == .4


def test_vad_value_error_still_retries_once_without_vad():
    asr, calls = make_asr([ValueError("VAD unavailable"), ([segment()], info())])
    transcript = asr.transcribe("unused.wav")
    assert [call["vad_filter"] for call in calls] == [True, False]
    assert len(transcript.segments) == 1


def test_existing_empty_vad_value_error_still_returns_no_speech():
    asr, calls = make_asr([ValueError("max() iterable argument is empty")])
    transcript = asr.transcribe("unused.wav")
    assert [call["vad_filter"] for call in calls] == [True]
    assert transcript.segments == []
    assert transcript.language == "en"
