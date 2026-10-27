import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

import app.render as render_module
from app.pipeline import probe
from app.render import fit_wav_atempo, wav_duration
from app.segments import Segment, merge_segments, plan_timing


def segment(identifier, start, end, text="fragment"):
    return Segment(identifier, start, end, text, "ខ្មែរ")


def write_tone(path: Path, seconds: float, rate: int = 48000) -> None:
    samples = int(seconds * rate)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"".join(struct.pack("<h", 1000) for _ in range(samples)))


def test_fit_stats_for_segment_that_already_fits():
    stat = plan_timing([segment(1, 1, 3)], [1.5])[0]
    assert stat["tempo"] == 1.0
    assert stat["fitted_duration"] == 1.5
    assert stat["gap_used"] == 0.0
    assert not stat["review_required"]
    assert {"target_duration", "raw_duration", "tempo", "fitted_duration", "overflow", "gap_used", "placement_start", "placement_end", "review_required"} <= stat.keys()


def test_slight_excess_fits_at_bounded_tempo():
    stat = plan_timing([segment(1, 0, 2)], [2.2])[0]
    assert stat["tempo"] == pytest.approx(1.1)
    assert stat["fitted_duration"] == pytest.approx(2.0)
    assert not stat["review_required"]


def test_severe_excess_requires_review_without_borrowing_pause():
    stats = plan_timing([segment(1, 0, 2), segment(2, 3, 4)], [3.0, 0.5])
    assert stats[0]["tempo"] == pytest.approx(1.15)
    assert stats[0]["overflow"] > 0
    assert stats[0]["review_required"]
    assert stats[0]["available_gap"] == pytest.approx(1.0)
    assert stats[0]["gap_used"] == 0.0


@pytest.mark.parametrize("start,end,raw", [(1, 1, 1), (-1, 1, 1), (0, math.inf, 1), (0, 1, 0)])
def test_invalid_timestamps_or_wav_durations_fail(start, end, raw):
    with pytest.raises(ValueError):
        plan_timing([segment(1, start, end)], [raw])


def test_tight_boundaries_and_pauses_are_preserved():
    stats = plan_timing([segment(1, 0, 1), segment(2, 1, 2), segment(3, 3, 4)], [1, 1, 1])
    assert [stat["placement_start"] for stat in stats] == [0, 1, 3]
    assert stats[1]["available_gap"] == 1
    assert all(stat["gap_used"] == 0 for stat in stats)
    assert all(not stat["review_required"] for stat in stats)


def test_source_overlap_requires_review_instead_of_mixing_over_it():
    stats = plan_timing([segment(1, 0, 2), segment(2, 1.9, 3)], [2, 1])
    assert stats[1]["review_required"]
    assert stats[1]["review_reason"] == "source_overlap"


def test_merge_only_contiguous_sentence_fragments():
    raw = [
        type("ASR", (), {"start": 0, "end": .8, "text": "hello"})(),
        type("ASR", (), {"start": .9, "end": 1.6, "text": "world."})(),
        type("ASR", (), {"start": 2.4, "end": 3, "text": "Separate."})(),
    ]
    merged = merge_segments(raw, max_gap=.35)
    assert [(item.start, item.end, item.source_text) for item in merged] == [(0.0, 1.6, "hello world."), (2.4, 3.0, "Separate.")]


def test_atempo_measures_real_output_without_linear_resampling(tmp_path):
    source, fitted = tmp_path / "source.wav", tmp_path / "fitted.wav"
    write_tone(source, 1.15)
    actual = fit_wav_atempo(source, fitted, 1.15)
    assert fitted.exists()
    assert actual == wav_duration(fitted)
    assert actual == pytest.approx(1.0, abs=.04)


def test_timing_review_reuses_the_existing_frontend_job_contract(monkeypatch):
    saved = {}
    monkeypatch.setattr(render_module, "update_job", lambda job_id, **changes: saved.update(changes))
    stats = plan_timing([segment(1, 0, 1)], [2])
    render_module._timing_review("synthetic", [segment(1, 0, 1)], ["nllb"], stats)
    assert saved["status"] == "awaiting_review"
    assert saved["error"]["code"] == "TIMING_OVERFLOW_REVIEW_REQUIRED"
    assert saved["timing_review"]["fit_stats"] == stats
    frontend = Path("frontend/src/App.jsx").read_text(encoding="utf-8")
    assert "job?.status === 'awaiting_review'" in frontend


def test_review_resume_never_reuses_a_stale_job_local_voice_file(tmp_path, monkeypatch):
    stale = tmp_path / "voice-0000.wav"
    write_tone(stale, .5)
    calls, updates = [], []

    def synthesize(text, voice, destination, *args, **kwargs):
        calls.append(text)
        write_tone(destination, 2.0)  # deliberately too long for the one-second slot
        return "edge-tts"

    monkeypatch.setattr(render_module, "synthesize_khmer", synthesize)
    monkeypatch.setattr(render_module, "require_real_speech", lambda _: None)
    monkeypatch.setattr(render_module, "get_job", lambda _: {"status": "running"})
    monkeypatch.setattr(render_module, "update_job", lambda _jid, **changes: updates.append(changes))
    monkeypatch.setattr("app.long_video.get_cached_segment_audio", lambda _: None)
    monkeypatch.setattr("app.long_video.store_cached_segment_audio", lambda *_: None)
    render_module.render("synthetic", tmp_path / "input.mp4", tmp_path, [segment(1, 0, 1)], 2,
                         {"voice": "test"}, type("Info", (), {"language": "en", "language_probability": 1})(),
                         ["manual"], {}, 0)
    assert calls == ["ខ្មែរ"]
    review = updates[-1]
    assert review["status"] == "awaiting_review"
    assert review["timing_review"]["fit_stats"][0]["raw_duration"] == pytest.approx(2.0)


def test_generated_media_has_expected_audio_and_video_streams(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is unavailable")
    video = tmp_path / "synthetic.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=size=64x64:rate=24:color=black", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-c:v", "libx264", "-c:a", "aac", str(video)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    metadata = probe(video)
    kinds = {stream["codec_type"] for stream in metadata["streams"]}
    assert {"audio", "video"} <= kinds
    assert float(metadata["format"]["duration"]) == pytest.approx(1.0, abs=.1)
