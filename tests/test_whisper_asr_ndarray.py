import os

os.environ.setdefault("ASR_BATCHED", "0")
os.environ.setdefault("ASR_BEAM_SIZE", "1")

import numpy as np
import pytest

from app.providers import WhisperASR, Transcript
from app.asr_chunking import ChunkedASR, create_chunks


class FakeInfo:
    def __init__(self, language="en", probability=0.9):
        self.language = language
        self.language_probability = probability


class FakeModel:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []
        self.transcribe_calls = []

    def transcribe(self, audio, *args, **kwargs):
        self.transcribe_calls.append((audio, args, kwargs))
        if not self.responses:
            return iter([]), FakeInfo()
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        segs, info = item
        return iter(segs), info


def make_asr(model=None):
    asr = WhisperASR.__new__(WhisperASR)
    asr.model = model if model is not None else FakeModel()
    asr._batched_pipe = None
    return asr


def seg(start, end, text):
    from types import SimpleNamespace
    return SimpleNamespace(start=start, end=end, text=text)


def test_ndarray_input_passes_array_directly():
    model = FakeModel([([(seg(0.0, 1.0, "hello"))], FakeInfo("en", 0.9))])
    asr = make_asr(model)
    arr = np.zeros(16000, dtype=np.float32)
    transcript = asr.transcribe(arr)
    assert isinstance(transcript, Transcript)
    assert len(transcript.segments) == 1
    passed_audio = model.transcribe_calls[0][0]
    assert isinstance(passed_audio, np.ndarray)
    assert passed_audio.dtype == np.float32
    assert passed_audio.shape == (16000,)


def test_file_path_input_still_works(tmp_path):
    import wave

    wav = tmp_path / "audio.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)

    model = FakeModel([([(seg(0.0, 1.0, "hello"))], FakeInfo("en", 0.9))])
    asr = make_asr(model)
    asr._load_wav = lambda audio: np.zeros(16000, dtype=np.float32)
    transcript = asr.transcribe(wav)
    assert len(transcript.segments) == 1
    passed_audio = model.transcribe_calls[0][0]
    assert isinstance(passed_audio, np.ndarray)


def test_empty_ndarray_returns_empty_transcript():
    model = FakeModel()
    asr = make_asr(model)
    arr = np.zeros(0, dtype=np.float32)
    transcript = asr.transcribe(arr)
    assert transcript.segments == []
    assert model.transcribe_calls == []


def test_invalid_ndarray_shape_raises():
    model = FakeModel()
    asr = make_asr(model)
    arr = np.zeros((2, 16000), dtype=np.float32)
    with pytest.raises(ValueError, match="1-D"):
        asr.transcribe(arr)


def test_non_floating_dtype_raises():
    model = FakeModel()
    asr = make_asr(model)
    arr = np.zeros(16000, dtype=np.int16)
    with pytest.raises(ValueError, match="floating-point"):
        asr.transcribe(arr)


def test_nan_samples_raise():
    model = FakeModel()
    asr = make_asr(model)
    arr = np.zeros(16000, dtype=np.float32)
    arr[0] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        asr.transcribe(arr)


def test_inf_samples_raise():
    model = FakeModel()
    asr = make_asr(model)
    arr = np.zeros(16000, dtype=np.float32)
    arr[0] = np.inf
    with pytest.raises(ValueError, match="NaN"):
        asr.transcribe(arr)


def test_incorrect_sample_rate_raises():
    model = FakeModel()
    asr = make_asr(model)
    arr = np.zeros(16000, dtype=np.float32)
    with pytest.raises(ValueError, match="16000"):
        asr.transcribe(arr, sample_rate=8000)


def test_chunked_asr_calls_whisper_asr_successfully():
    model = FakeModel([([(seg(0.0, 1.0, "hi"))], FakeInfo("en", 0.9))])
    asr = make_asr(model)
    # Simulate ChunkedASR using WhisperASR instance directly.
    chunked = ChunkedASR(asr, chunk_duration=300.0, overlap=1.5, max_retries=0)
    from pathlib import Path
    import tempfile, wave

    fd, name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    path = Path(name)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)
    try:
        transcript = chunked.transcribe(path, source_lang="auto", duration=1.0)
        assert len(transcript.segments) == 1
        assert model.transcribe_calls, "WhisperASR.transcribe was not called"
    finally:
        path.unlink(missing_ok=True)
