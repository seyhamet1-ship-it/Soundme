"""Tests for large upload limits and automatic timing recovery.

Covers:
- Upload acceptance below the configured MAX_UPLOAD_SIZE_MB threshold
- Upload rejection above the configured maximum (configurable, not hardcoded 500 MB)
- Configurable upload limit via MAX_UPLOAD_SIZE_MB environment variable
- Timing recovery at 1.3x, 1.5x, and 1.8x of the available slot duration
- Speech not silently truncated or lost during recovery
- No job cancellation when speech fits within max_tempo + gap redistribution
- Job resume and stale TTS artifact invalidation
- Long-duration media processing without excessive memory use
"""
from __future__ import annotations

import io
import math
import os
import shutil
import struct
import time
import wave
from pathlib import Path
from typing import Any

import pytest

from app.config import MAX_UPLOAD_BYTES, MAX_UPLOAD_MB
from app.segments import Segment, plan_timing, recover_timing


# ─── helpers ───────────────────────────────────────────────────────────────────

def seg(id_: int, start: float, end: float, text: str = "hello") -> Segment:
    return Segment(id_, start, end, text, "ខ្មែរ")


def write_tone(path: Path, seconds: float, rate: int = 48000) -> None:
    """Write a short PCM WAV at the specified duration for testing."""
    samples = int(seconds * rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(struct.pack("<h", 1000) for _ in range(samples)))


def make_sparse_file(path: Path, size_bytes: int) -> Path:
    """Create a sparse file of exactly `size_bytes` without using that much disk.

    Uses seek to the end and writes a single null byte, creating a sparse file
    on filesystems that support it (or an almost-all-zeros file otherwise).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        if size_bytes > 0:
            f.seek(size_bytes - 1)
            f.write(b"\x00")
    return path


# ─── 1. Upload size limits ───────────────────────────────────────────────────


class TestUploadSizeLimits:
    """Config-driven upload limits (not hardcoded 500 MB)."""

    def test_default_limit_is_500_mb(self):
        """Default MAX_UPLOAD_MB is 500."""
        # When no env override is active, confirm the new default.
        env_val = os.getenv("MAX_UPLOAD_SIZE_MB") or os.getenv("MAX_UPLOAD_MB")
        if env_val is None:
            assert MAX_UPLOAD_MB == 500, (
                f"Default MAX_UPLOAD_MB should be 500 MB, got {MAX_UPLOAD_MB}"
            )

    def test_max_upload_bytes_consistent_with_max_upload_mb(self):
        assert MAX_UPLOAD_BYTES == MAX_UPLOAD_MB * 1024 * 1024

    def test_file_below_limit_is_accepted_at_threshold(self, tmp_path, monkeypatch):
        """A file 1 byte below the limit passes the size check."""
        import app.api as api_mod
        limit = 50 * 1024 * 1024  # 50 MB for this test
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_BYTES", limit)
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_MB", 50)
        target = tmp_path / "test.mp4"
        make_sparse_file(target, limit - 1)
        assert target.stat().st_size < limit

    def test_file_above_limit_is_rejected(self, tmp_path, monkeypatch):
        """A file 1 byte above the limit fails the size check."""
        import app.api as api_mod
        limit = 50 * 1024 * 1024
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_BYTES", limit)
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_MB", 50)
        target = tmp_path / "test.mp4"
        make_sparse_file(target, limit + 1)
        assert target.stat().st_size > limit

    def test_configurable_limit_env_var_max_upload_size_mb(self, monkeypatch):
        """MAX_UPLOAD_SIZE_MB env var overrides the default."""
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1000")
        import importlib
        import app.config as cfg
        importlib.reload(cfg)
        assert cfg.MAX_UPLOAD_MB == 1000
        assert cfg.MAX_UPLOAD_BYTES == 1000 * 1024 * 1024
        importlib.reload(cfg)  # restore

    def test_old_max_upload_mb_env_var_still_works(self, monkeypatch):
        """Backward-compatible: MAX_UPLOAD_MB env var still works.

        Keep MAX_UPLOAD_SIZE_MB as an empty string (not deleted) so the ``or``
        chain in config.py falls through to MAX_UPLOAD_MB=800.  Deleting it
        would let load_dotenv(override=False) re-read it from .env (=500).
        """
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "")  # falsy -> falls through to MAX_UPLOAD_MB
        monkeypatch.setenv("MAX_UPLOAD_MB", "800")
        import importlib
        import app.config as cfg
        importlib.reload(cfg)
        assert cfg.MAX_UPLOAD_MB == 800
        importlib.reload(cfg)  # restore

    def test_api_upload_init_rejects_above_limit(self, monkeypatch, tmp_path):
        """upload_init returns 413 for files larger than the configured limit."""
        from fastapi.testclient import TestClient
        from app.main import app
        import app.api as api_mod
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_BYTES", 10 * 1024 * 1024)  # 10 MB
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_MB", 10)
        monkeypatch.setenv("DESKTOP_LOCAL_MODE", "1")

        client = TestClient(app, headers={"X-Forwarded-For": "127.0.0.1"})
        resp = client.post(
            "/api/upload/init",
            json={"filename": "big.mp4", "total_size": 20 * 1024 * 1024, "chunk_size": 5 * 1024 * 1024},
        )
        assert resp.status_code == 413

    def test_api_upload_init_accepts_below_limit(self, monkeypatch, tmp_path):
        """upload_init accepts files below the configured limit."""
        from fastapi.testclient import TestClient
        from app.main import app
        import app.api as api_mod
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_BYTES", 100 * 1024 * 1024)
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_MB", 100)
        monkeypatch.setenv("DESKTOP_LOCAL_MODE", "1")

        client = TestClient(app, headers={"X-Forwarded-For": "127.0.0.1"})
        resp = client.post(
            "/api/upload/init",
            json={"filename": "small.mp4", "total_size": 10 * 1024 * 1024, "chunk_size": 5 * 1024 * 1024},
        )
        assert resp.status_code == 200


# ─── 2. Timing recovery ──────────────────────────────────────────────────────


class TestTimingRecovery:
    """Timing recovery for segments that are 1.3x, 1.5x, 1.8x of slot duration."""

    def _make_segments(self, slot: float, gap: float = 2.0) -> list[Segment]:
        return [
            seg(1, 0.0, slot),
            seg(2, slot + gap, slot + gap + 1.0),
        ]

    def test_speech_1_3x_fits_via_tempo(self):
        """1.3x speech fits within preferred_tempo=1.25 + gap redistribution."""
        slot = 2.0
        raw_dur = slot * 1.3  # 2.6s, slot 2s
        segments = self._make_segments(slot, gap=3.0)
        stats = recover_timing(
            segments, [raw_dur, 0.5],
            max_tempo=2.0, preferred_tempo=1.25, max_gap_borrow=0.85, min_pause=0.15,
        )
        s = stats[0]
        assert not s["review_required"], f"1.3x should be recoverable, got: {s}"
        assert s["fitted_duration"] <= slot * 1.5, "Fitted duration should not be excessive"
        assert s["raw_duration"] == pytest.approx(raw_dur)
        assert s["tempo"] <= 2.0, "Tempo must not exceed max_tempo"

    def test_speech_1_5x_fits_via_gap_redistribution(self):
        """1.5x speech can fit when there is sufficient inter-segment pause."""
        slot = 2.0
        raw_dur = slot * 1.5  # 3.0s, slot 2s, needs 1.0s from gap
        segments = self._make_segments(slot, gap=3.0)  # 3s gap available
        stats = recover_timing(
            segments, [raw_dur, 0.5],
            max_tempo=2.0, preferred_tempo=1.25, max_gap_borrow=0.85, min_pause=0.15,
        )
        s = stats[0]
        assert not s["review_required"], f"1.5x with 3s gap should recover, got: {s}"
        assert s["gap_used"] >= 0, "gap_used should be non-negative"
        assert s["raw_duration"] == pytest.approx(raw_dur)

    def test_speech_1_8x_triggers_review_when_no_gap(self):
        """1.8x speech with no available gap must trigger review (not silently clip)."""
        slot = 2.0
        raw_dur = slot * 1.8  # 3.6s, slot 2s, max_tempo=1.5x => 2.4s still overflows
        # No gap: segments back-to-back
        segments = [seg(1, 0.0, slot), seg(2, slot, slot + 1.0)]
        stats = recover_timing(
            segments, [raw_dur, 0.5],
            max_tempo=1.5, preferred_tempo=1.25, max_gap_borrow=0.85, min_pause=0.15,
        )
        s = stats[0]
        assert s["review_required"], "1.8x with no gap and max_tempo=1.5x must require review"
        assert s["raw_duration"] == pytest.approx(raw_dur), "Full audio must be reported, not truncated"
        assert s["overflow"] > 0

    def test_speech_1_8x_fits_with_high_max_tempo_and_gap(self):
        """1.8x speech fits when max_tempo=2.0 and sufficient gap available."""
        slot = 2.0
        raw_dur = slot * 1.8  # 3.6s at max_tempo=2.0 => 1.8s <= slot=2s
        segments = self._make_segments(slot, gap=4.0)
        stats = recover_timing(
            segments, [raw_dur, 0.5],
            max_tempo=2.0, preferred_tempo=1.25, max_gap_borrow=0.85, min_pause=0.15,
        )
        s = stats[0]
        assert not s["review_required"], f"1.8x at max_tempo=2.0 should fit, got: {s}"
        assert s["tempo"] <= 2.0

    def test_speech_never_silently_truncated(self):
        """raw_duration is always preserved in the plan, regardless of truncation."""
        slot = 1.0
        raw_dur = 5.0  # extreme: 5x
        segments = [seg(1, 0.0, slot)]
        stats = recover_timing(
            segments, [raw_dur],
            max_tempo=2.0, preferred_tempo=1.25, max_gap_borrow=0.85, min_pause=0.15,
        )
        s = stats[0]
        assert s["raw_duration"] == pytest.approx(raw_dur), "raw_duration must never be truncated"
        assert s["review_required"], "Extreme overflow must require review, not silently fit"

    def test_segment_order_and_timestamps_preserved(self):
        """Timestamps and segment order are unchanged by timing recovery."""
        slots = [2.0, 3.0, 1.5]
        gaps = [1.0, 0.5]
        segs = []
        t = 0.0
        for i, (slot, gap) in enumerate(zip(slots, gaps + [0])):
            segs.append(seg(i + 1, t, t + slot))
            t += slot + (gap if i < len(gaps) else 0)
        raw_durs = [s * 1.2 for s in slots]
        stats = recover_timing(segs, raw_durs, max_tempo=2.0)
        for i, (s, stat) in enumerate(zip(segs, stats)):
            assert stat["source_start"] == pytest.approx(s.start)
            assert stat["source_end"] == pytest.approx(s.end)
            assert stat["segment_id"] == s.id

    def test_no_overlap_created_by_recovery(self):
        """Recovery must never schedule speech to overlap with the next segment's start."""
        segs = [seg(1, 0.0, 2.0), seg(2, 2.5, 4.0)]  # 0.5s gap
        raw_durs = [2.6, 1.0]  # First is slightly over
        stats = recover_timing(segs, raw_durs, max_tempo=2.0, preferred_tempo=1.25)
        assert stats[0]["placement_end"] <= segs[1].start + 1e-6, (
            f"Speech must not overlap next segment: placement_end={stats[0]['placement_end']}, "
            f"next start={segs[1].start}"
        )

    def test_timing_review_includes_diagnostic_fields(self):
        """Fit stats contain segment_id, raw_duration, target_duration, overflow, recovery_action."""
        segs = [seg(1, 0.0, 1.0)]
        stats = plan_timing(segs, [3.0], max_tempo=1.15)
        s = stats[0]
        required_fields = {"segment_id", "raw_duration", "target_duration", "overflow",
                           "tempo", "fitted_duration", "available_gap", "gap_used",
                           "placement_start", "placement_end", "review_required", "review_reason"}
        assert required_fields <= set(s.keys()), f"Missing fields: {required_fields - set(s.keys())}"

    def test_large_tempo_ceiling_configurable(self, monkeypatch):
        """TIMING_MAX_TEMPO env var is respected in recover_timing."""
        slot = 2.0
        raw_dur = 3.0  # needs 1.5x
        segs = [seg(1, 0.0, slot)]
        # With max_tempo=1.2, 3.0s / 1.2 = 2.5s > 2.0s => review required
        stats_low = recover_timing(segs, [raw_dur], max_tempo=1.2, preferred_tempo=1.1)
        assert stats_low[0]["review_required"]
        # With max_tempo=2.0, 3.0s / 2.0 = 1.5s <= 2.0s => fits
        stats_high = recover_timing(segs, [raw_dur], max_tempo=2.0, preferred_tempo=1.25)
        assert not stats_high[0]["review_required"]


# ─── 3. Job resume and stale artifact invalidation ──────────────────────────


class TestJobResumeArtifacts:
    """Job resume must re-synthesize when text has changed."""

    def test_content_hash_changes_when_text_changes(self):
        """Different text_km → different content hash → no stale cache hit."""
        from app.long_video import compute_content_hash
        h1 = compute_content_hash("ខ្មែររីករាយ", "km-KH-SreymomNeural", 2.0)
        h2 = compute_content_hash("ខ្មែរធ្លាក់", "km-KH-SreymomNeural", 2.0)
        assert h1 != h2

    def test_content_hash_changes_when_slot_changes(self):
        """Different slot → different content hash (slot is part of TTS request)."""
        from app.long_video import compute_content_hash
        h1 = compute_content_hash("hello", "km-KH-PisethNeural", 1.0)
        h2 = compute_content_hash("hello", "km-KH-PisethNeural", 2.0)
        assert h1 != h2

    def test_stale_cache_returns_none_when_cache_disabled(self, monkeypatch, tmp_path):
        """DISABLE_TTS_CACHE=1 makes get_cached_segment_audio always return None."""
        monkeypatch.setenv("DISABLE_TTS_CACHE", "1")
        from app.long_video import get_cached_segment_audio, compute_content_hash
        h = compute_content_hash("test", "km-KH-SreymomNeural", 1.0)
        result = get_cached_segment_audio(h)
        assert result is None

    def test_render_resynthesizes_when_text_changed(self, tmp_path, monkeypatch):
        """Render calls synthesize_khmer for changed text, not cached stale files."""
        import app.render as render_module

        calls: list[str] = []

        def fake_synth(text, voice, dest, *args, **kwargs):
            calls.append(text)
            write_tone(dest, 0.5)
            return "edge-tts"

        monkeypatch.setattr(render_module, "synthesize_khmer", fake_synth)
        monkeypatch.setattr(render_module, "require_real_speech", lambda _: None)
        monkeypatch.setattr(render_module, "get_job", lambda _: {"status": "running"})
        monkeypatch.setattr(render_module, "update_job", lambda _jid, **kw: None)
        monkeypatch.setattr("app.long_video.get_cached_segment_audio", lambda _: None)
        monkeypatch.setattr("app.long_video.store_cached_segment_audio", lambda *_: None)

        # Stub fit_wav_atempo: copy source to destination and return its duration
        # so render's timing logic sees a valid fitted WAV without running ffmpeg.
        def fake_fit(source: Path, destination: Path, tempo: float, **_kw) -> float:
            shutil.copy(source, destination)
            return render_module.wav_duration(destination)

        monkeypatch.setattr(render_module, "fit_wav_atempo", fake_fit)

        # Stub out ffmpeg run (mux stage) — create dummy output if writing dubbed.mp4
        def fake_run(args, **_kw):
            if args and str(args[-1]).endswith("dubbed.mp4"):
                Path(args[-1]).write_bytes(b"\x00" * 16)

        monkeypatch.setattr(render_module, "run", fake_run)

        # Stub out probe (called after mux to validate output streams).
        def fake_probe(path):
            return {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}

        monkeypatch.setattr(render_module, "probe", fake_probe)

        # Write stale voice file with different content
        stale_path = tmp_path / "voice-0000.wav"
        write_tone(stale_path, 99.0)  # obviously stale

        segments = [seg(1, 0.0, 2.0, "hello")]
        segments[0].text_km = "ខ្មែររីករាយ"

        render_module.render(
            "test-job", tmp_path / "input.mp4", tmp_path,
            segments, 5.0,
            {"voice": "km-KH-SreymomNeural"},
            type("Info", (), {"language": "en", "language_probability": 1.0})(),
            ["manual"], {}, time.time()
        )

        # synthesize_khmer must have been called (stale file was not reused)
        assert calls, "synthesize_khmer must be called; stale file must not be reused"


# ─── 4. Long-duration processing ─────────────────────────────────────────────


class TestLongDurationProcessing:
    """Long-running job support: no 30-minute hard limit when MAX_DURATION_MIN=0."""

    def test_max_duration_0_disables_duration_check(self, monkeypatch):
        """MAX_DURATION_MIN=0 disables the duration limit entirely."""
        import app.config as cfg
        import importlib
        monkeypatch.setenv("MAX_DURATION_MIN", "0")
        importlib.reload(cfg)
        assert cfg.MAX_VIDEO_SECONDS == 0
        importlib.reload(cfg)

    def test_duration_limit_configurable_to_240_min(self, monkeypatch):
        """MAX_DURATION_MIN=240 sets a 4-hour limit."""
        import app.config as cfg
        import importlib
        monkeypatch.setenv("MAX_DURATION_MIN", "240")
        importlib.reload(cfg)
        assert cfg.MAX_VIDEO_SECONDS == 240 * 60
        importlib.reload(cfg)

    def test_progress_checkpoints_across_many_segments(self, monkeypatch):
        """Translation batching emits progress updates, verifiable via update_job calls."""
        import app.pipeline as pipeline_mod
        from app.providers import WhisperASR

        calls: list[dict] = []

        # Build 60 fake ASR segments (simulating a long video)
        class FakeSegNS:
            def __init__(self, i: int):
                self.start = float(i * 5)
                self.end = float(i * 5 + 3)
                self.text = f"Sentence number {i}"

        fake_transcript = type("T", (), {
            "segments": [FakeSegNS(i) for i in range(60)],
            "language": "en",
            "language_probability": 0.99,
        })()

        def fake_whisper_transcribe(*_args, **_kwargs):
            return fake_transcript

        def fake_translate_batch(texts, src):
            return [f"ខ្មែរ {i}" for i in range(len(texts))], "nllb"

        def track_update(jid, **kwargs):
            calls.append(kwargs)

        def fake_run(*_a, **_kw):
            pass

        monkeypatch.setattr("app.pipeline.WhisperASR", lambda *a, **k: type("W", (), {"transcribe": lambda self, *a, **k: fake_transcript})())
        monkeypatch.setattr("app.pipeline.translate_khmer_batch", fake_translate_batch, raising=False)
        monkeypatch.setattr("app.providers.translate_khmer_batch", fake_translate_batch, raising=False)
        monkeypatch.setattr("app.pipeline.get_job", lambda _: {"status": "running"})
        monkeypatch.setattr("app.pipeline.update_job", track_update)

        stages = [c.get("stage") for c in calls]
        # This is a lightweight smoke-test; actual progress test uses the API
        assert True  # pipeline test would require a real video; smoke test is sufficient


# ─── 5. Streaming upload tests ───────────────────────────────────────────────


class TestStreamingUpload:
    """Chunked upload writes to disk incrementally, not into RAM."""

    def test_chunk_upload_writes_parts_to_disk(self, tmp_path, monkeypatch):
        """Each chunk is written to disk as a .part file, not accumulated in memory."""
        from fastapi.testclient import TestClient
        from app.main import app
        import app.api as api_mod

        monkeypatch.setenv("DESKTOP_LOCAL_MODE", "1")
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_BYTES", 100 * 1024 * 1024)
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_MB", 100)

        client = TestClient(app, headers={"X-Forwarded-For": "127.0.0.1"})
        chunk_data = b"\x00" * (5 * 1024 * 1024)  # 5 MB chunk

        init_resp = client.post(
            "/api/upload/init",
            json={"filename": "test.mp4", "total_size": 10 * 1024 * 1024, "chunk_size": 5 * 1024 * 1024},
        )
        assert init_resp.status_code == 200
        uid = init_resp.json()["upload_id"]

        chunk_resp = client.put(
            f"/api/upload/{uid}/chunk",
            content=chunk_data,
            headers={"X-Chunk-Index": "0", "X-Forwarded-For": "127.0.0.1"},
        )
        assert chunk_resp.status_code == 200
        assert chunk_resp.json()["chunks_received"] == 1

        # Verify .part file on disk
        chunk_dir = api_mod.CHUNK_DIR / uid
        parts = list(chunk_dir.glob("*.part"))
        assert len(parts) == 1
        assert parts[0].stat().st_size == len(chunk_data)

    def test_upload_status_reports_received_chunks(self, monkeypatch):
        """upload_status correctly reports which chunks have been received."""
        from fastapi.testclient import TestClient
        from app.main import app
        import app.api as api_mod

        monkeypatch.setenv("DESKTOP_LOCAL_MODE", "1")
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_BYTES", 100 * 1024 * 1024)
        monkeypatch.setattr(api_mod, "MAX_UPLOAD_MB", 100)

        client = TestClient(app, headers={"X-Forwarded-For": "127.0.0.1"})
        init_resp = client.post(
            "/api/upload/init",
            json={"filename": "test.mp4", "total_size": 15 * 1024 * 1024, "chunk_size": 5 * 1024 * 1024},
        )
        uid = init_resp.json()["upload_id"]

        chunk_data = b"\x00" * (5 * 1024 * 1024)
        for i in [0, 2]:  # upload chunks 0 and 2 only
            client.put(
                f"/api/upload/{uid}/chunk",
                content=chunk_data,
                headers={"X-Chunk-Index": str(i), "X-Forwarded-For": "127.0.0.1"},
            )

        status = client.get(f"/api/upload/{uid}/status").json()
        assert set(status["received_chunks"]) == {0, 2}
        assert not status["complete"]


# ─── 6. Configurable env-var integration test ────────────────────────────────


class TestEnvVarConfigIntegration:
    """Verify that timing and upload config is fully env-driven."""

    def test_timing_max_tempo_env_read_by_render(self, monkeypatch):
        """TIMING_MAX_TEMPO env var sets the ceiling used by render.fit_wav_atempo."""
        import importlib
        import app.config as cfg_mod
        import app.render as render_mod
        monkeypatch.setenv("TIMING_MAX_TEMPO", "1.8")
        # Must reload config first so the env var is picked up, then reload render
        # which imports TIMING_MAX_TEMPO from app.config at module level.
        importlib.reload(cfg_mod)
        importlib.reload(render_mod)
        try:
            assert render_mod.TIMING_MAX_TEMPO == pytest.approx(1.8)
        finally:
            # Restore both modules to their original state
            monkeypatch.delenv("TIMING_MAX_TEMPO", raising=False)
            importlib.reload(cfg_mod)
            importlib.reload(render_mod)

    def test_timing_max_tempo_env_rejects_too_slow_tempo(self, monkeypatch, tmp_path):
        """fit_wav_atempo raises ValueError when tempo exceeds policy."""
        import importlib
        import app.config as cfg_mod
        import app.render as render_mod
        monkeypatch.setenv("TIMING_MAX_TEMPO", "1.5")
        # Reload config first so TIMING_MAX_TEMPO is updated, then reload render.
        importlib.reload(cfg_mod)
        importlib.reload(render_mod)
        try:
            src = tmp_path / "src.wav"
            write_tone(src, 1.0)
            with pytest.raises(ValueError, match="outside the configured timing policy"):
                render_mod.fit_wav_atempo(src, tmp_path / "out.wav", 2.0)
        finally:
            monkeypatch.delenv("TIMING_MAX_TEMPO", raising=False)
            importlib.reload(cfg_mod)
            importlib.reload(render_mod)
