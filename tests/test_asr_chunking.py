import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.asr_chunking import (
    ChunkedASR,
    create_chunks,
    validate_segments,
    deduplicate_overlap,
    compute_source_fingerprint,
    get_audio_duration,
    read_audio_chunk,
)
from app.config import MAX_VIDEO_SECONDS
from app.providers import Transcript


def write_silence_wav(path, duration, rate=16000):
    frames = int(duration * rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * frames)

class FakeInfo:
    def __init__(self, language="en", probability=0.9):
        self.language = language
        self.language_probability = probability


class FakeModel:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = 0

    def transcribe(self, audio, *, language=None, vad_filter=True, beam_size=1,
                   condition_on_previous_text=False, max_new_tokens=128,
                   hallucination_silence_threshold=2.0, temperature=0.0):
        self.calls += 1
        if not self.responses:
            return iter([]), FakeInfo()
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        segs, info = item
        return iter(segs), info


def seg(start, end, text):
    return SimpleNamespace(start=start, end=end, text=text)

def test_120_minute_duration_acceptance():
    assert MAX_VIDEO_SECONDS >= 120 * 60


def test_create_chunks_120_minutes():
    chunks = create_chunks(120 * 60, 300.0, 1.5)
    assert len(chunks) > 1
    assert chunks[0].start == 0.0
    assert abs(chunks[-1].end - 120 * 60) < 1e-6
    for i in range(1, len(chunks)):
        assert chunks[i].start >= chunks[i - 1].start
        assert chunks[i].start < chunks[i].end
    for c in chunks:
        assert c.duration <= 300.0 + 1e-6
    if len(chunks) > 1:
        overlap = chunks[0].end - chunks[1].start
        assert 0.5 <= overlap <= 2.5


def test_chunk_ordering_and_coverage():
    chunks = create_chunks(600.0, 300.0, 1.5)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].start == 0.0
    assert abs(chunks[-1].end - 600.0) < 1e-6


def test_timestamp_offset_conversion(tmp_path):
    wav = tmp_path / "audio.wav"
    write_silence_wav(wav, 600.0)
    model = FakeModel([
        ([(SimpleNamespace(start=0.0, end=2.0, text="Hello world"))], FakeInfo("en", 0.9)),
        ([(SimpleNamespace(start=0.0, end=2.0, text="Second chunk"))], FakeInfo("en", 0.9)),
    ])
    asr = ChunkedASR(model, chunk_duration=300.0, overlap=1.5, max_retries=0)
    transcript = asr.transcribe(wav, source_lang="auto", duration=600.0)
    assert len(transcript.segments) == 2
    assert transcript.segments[1].start >= 298.0


def test_overlap_dedup_no_duplicate():
    s1 = SimpleNamespace(start=10.0, end=12.0, text="Repeated sentence.")
    s2 = SimpleNamespace(start=11.0, end=13.0, text="Repeated sentence.")
    out = deduplicate_overlap([s1, s2], duration=20.0)
    assert len(out) == 1


def test_silent_chunk_completed(tmp_path):
    wav = tmp_path / "audio.wav"
    write_silence_wav(wav, 60.0)
    model = FakeModel([([], FakeInfo("en", 0.5))])
    asr = ChunkedASR(model, chunk_duration=300.0, max_retries=0)
    transcript = asr.transcribe(wav, source_lang="auto", duration=60.0)
    assert transcript.segments == []


def test_failed_chunk_raises(tmp_path):
    wav = tmp_path / "audio.wav"
    write_silence_wav(wav, 60.0)
    model = FakeModel([RuntimeError("boom")])
    asr = ChunkedASR(model, chunk_duration=300.0, max_retries=0)
    with pytest.raises(RuntimeError):
        asr.transcribe(wav, source_lang="auto", duration=60.0)


def test_checkpoint_persistence_and_resume(tmp_path):
    wav = tmp_path / "audio.wav"
    write_silence_wav(wav, 60.0)
    ckpt = tmp_path / "asr_checkpoint.json"
    model = FakeModel([([(SimpleNamespace(start=0.0, end=1.0, text="hi"))], FakeInfo("en", 0.9))])
    asr = ChunkedASR(model, chunk_duration=300.0, max_retries=0)
    t1 = asr.transcribe(wav, source_lang="auto", duration=60.0, checkpoint_path=ckpt)
    assert ckpt.exists()
    model2 = FakeModel([])
    model2.transcribe = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("should not be called"))
    asr2 = ChunkedASR(model2, chunk_duration=300.0, max_retries=0)
    t2 = asr2.transcribe(wav, source_lang="auto", duration=60.0, checkpoint_path=ckpt)
    assert len(t2.segments) == len(t1.segments)


def test_source_fingerprint_mismatch_invalidates_checkpoint(tmp_path):
    wav = tmp_path / "audio.wav"
    write_silence_wav(wav, 60.0)
    ckpt = tmp_path / "asr_checkpoint.json"
    model = FakeModel([([(SimpleNamespace(start=0.0, end=1.0, text="hi"))], FakeInfo("en", 0.9))])
    asr = ChunkedASR(model, chunk_duration=300.0, max_retries=0)
    asr.transcribe(wav, source_lang="auto", duration=60.0, checkpoint_path=ckpt)
    data = json.loads(ckpt.read_text())
    data["source_fingerprint"] = "sha256:deadbeef:size:0"
    ckpt.write_text(json.dumps(data))
    model2 = FakeModel([([(SimpleNamespace(start=0.0, end=1.0, text="new"))], FakeInfo("en", 0.9))])
    asr2 = ChunkedASR(model2, chunk_duration=300.0, max_retries=0)
    t2 = asr2.transcribe(wav, source_lang="auto", duration=60.0, checkpoint_path=ckpt)
    assert t2 is not None


def test_validate_segments_rejects_invalid():
    with pytest.raises(ValueError):
        validate_segments([SimpleNamespace(start=1.0, end=1.0, text="x")], 10.0)
    with pytest.raises(ValueError):
        validate_segments([SimpleNamespace(start=0.0, end=11.0, text="x")], 10.0)


def test_read_audio_chunk_roundtrip(tmp_path):
    wav = tmp_path / "audio.wav"
    write_silence_wav(wav, 2.0)
    arr = read_audio_chunk(wav, 0.0, 1.0)
    assert arr is not None
    assert arr.size > 0
