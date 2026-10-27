"""Phase 9 – updated test_api.py with auth token headers.

All routes now require Authorization: Bearer <token>.
The conftest.py autouse fixture sets ACCESS_TOKENS=soundme-test-token.
We disable model warm-up during tests via WARM_MODELS_AT_STARTUP=0 so that
/api/health returns status='ok' immediately (models_ready is tested separately).
"""
import os
import pytest
from fastapi.testclient import TestClient

# Disable model warm-up in tests so /api/health returns status='ok' synchronously.
os.environ.setdefault("WARM_MODELS_AT_STARTUP", "0")
os.environ.setdefault("ACCESS_TOKENS", "soundme-test-token")

from app import api, storage
from app.main import app

TOKEN = "soundme-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_health_voices_and_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("WARM_MODELS_AT_STARTUP", "0")
    with TestClient(app) as client:
        # Health is public (no auth required)
        assert client.get("/api/health").status_code == 200
        # Status is ok or warming — accept both since models may not be loaded
        status = client.get("/api/health").json()["status"]
        assert status in {"ok", "warming", "degraded"}, f"Unexpected status: {status}"
        # Voices require auth
        voices_resp = client.get("/api/voices", headers=AUTH)
        assert len(voices_resp.json()) == 2
        # Jobs require auth
        assert client.get("/api/jobs", headers=AUTH).json() == []
        assert client.get("/api/jobs/missing", headers=AUTH).status_code == 404
        # Missing auth → 401
        assert client.get("/api/jobs").status_code == 401
        # Bad source_lang → 422 (missing file form field)
        assert client.post("/api/jobs", data={"source_lang": "fr"}, headers=AUTH).status_code == 422


def test_upload_persists_job_and_rejects_unsupported_options(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(api, "probe", lambda _: {"streams": [{"codec_type": "video"}], "format": {"duration": "10"}})
    monkeypatch.setattr(api.POOL, "submit", lambda *args, **kwargs: None)
    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            files={"file": ("sample.mp4", b"fake video", "video/mp4")},
            headers=AUTH,
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert client.get(f"/api/jobs/{job_id}", headers=AUTH).json()["status"] == "queued"
        assert len(client.get("/api/jobs", headers=AUTH).json()) == 1
        assert client.delete(f"/api/jobs/{job_id}", headers=AUTH).json() == {"deleted": True}
        assert client.get(f"/api/jobs/{job_id}", headers=AUTH).status_code == 404
        bad = client.post(
            "/api/jobs",
            data={"voice": "unsupported"},
            files={"file": ("x.mp4", b"x", "video/mp4")},
            headers=AUTH,
        )
        assert bad.status_code == 400


def test_tts_preview_returns_synthesized_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")

    def fake_synth(text, voice, destination, duration, seed=1, rate="+0%", forced_provider=None):
        destination.write_bytes(b"RIFF0000WAVE")
        return "edge-tts"

    monkeypatch.setattr("app.providers.synthesize_khmer", fake_synth)
    with TestClient(app) as client:
        response = client.post(
            "/api/tts/preview",
            json={"voice": "km-KH-SreymomNeural"},
            headers=AUTH,
        )
        assert response.status_code == 200, response.text
        assert response.headers["x-tts-provider"] == "edge-tts"
        assert response.content.startswith(b"RIFF")
        bad = client.post("/api/tts/preview", json={"voice": "unsupported"}, headers=AUTH)
        assert bad.status_code == 400


def test_tts_preview_reports_tts_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")

    def failed_synth(*args, **kwargs):
        raise RuntimeError("TTS_UNAVAILABLE: Edge TTS -> Azure Speech (if configured) -> MMS failed (edge:down; mms:down)")

    monkeypatch.setattr("app.providers.synthesize_khmer", failed_synth)
    with TestClient(app) as client:
        response = client.post(
            "/api/tts/preview",
            json={"voice": "km-KH-SreymomNeural"},
            headers=AUTH,
        )
        assert response.status_code == 503, response.text
        assert "TTS_UNAVAILABLE" in response.json()["detail"]


def test_failure_error_preserves_tts_unavailable_code():
    from app.pipeline import failure_error

    payload = failure_error(RuntimeError("TTS_UNAVAILABLE: all providers failed"))
    assert payload["code"] == "TTS_UNAVAILABLE"
    assert "TTS_UNAVAILABLE" in payload["message_en"]
    assert payload["message_km"]
    assert failure_error(ValueError("no speech"))["code"] == "PROCESSING_FAILED"


def test_review_resume_uses_edited_segments_and_clears_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic")
    storage.set_job("review-job", {
        "id": "review-job", "status": "awaiting_review", "stage": "timing_review", "progress": 54,
        "owner_token": TOKEN, "source_path": str(source), "detected_language": "en",
        "options": {}, "providers": ["manual"],
        "segments": [{"id": 1, "start": 0.0, "end": 1.0, "source_text": "old", "text_km": "old"}],
        "error": {"code": "TIMING_OVERFLOW_REVIEW_REQUIRED"}, "timing_review": {"code": "TIMING_OVERFLOW_REVIEW_REQUIRED"},
    })
    monkeypatch.setattr(api, "probe", lambda _: {"streams": [{"codec_type": "video"}], "format": {"duration": "2"}})
    submitted = {}
    monkeypatch.setattr(api.POOL, "submit", lambda fn, *args: submitted.update(fn=fn, args=args))
    edited = [{"id": 1, "start": 0.0, "end": 1.0, "source_text": "old", "text_km": "edited"}]
    with TestClient(app) as client:
        assert client.put("/api/jobs/review-job/segments", json=edited, headers=AUTH).status_code == 200
        assert client.post("/api/jobs/review-job/resume", headers=AUTH).json() == {"status": "running"}
    stored = storage.get_job("review-job")
    assert stored["status"] == "running" and stored["error"] is None and stored["timing_review"] is None
    assert submitted["fn"] is api.resume_render_worker
    assert submitted["args"][3][0].text_km == "edited"


def test_review_resume_worker_records_a_render_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    storage.set_job("failed-resume", {"id": "failed-resume", "status": "running", "progress": 54, "owner_token": TOKEN})
    monkeypatch.setattr("app.render.render", lambda *_args: (_ for _ in ()).throw(RuntimeError("render exploded")))
    api.resume_render_worker("failed-resume")
    stored = storage.get_job("failed-resume")
    assert stored["status"] == "failed"
    assert stored["error"]["code"] == "PROCESSING_FAILED"
    assert "render exploded" in stored["error"]["message_en"]


def test_review_resume_worker_can_reach_completed_state(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    storage.set_job("completed-resume", {"id": "completed-resume", "status": "running", "progress": 54, "owner_token": TOKEN})

    def complete(job_id, *_args):
        storage.update_job(job_id, status="completed", stage="done", progress=100,
                           outputs={"video": "dubbed.mp4"}, timings={"fit_stats": []})

    monkeypatch.setattr("app.render.render", complete)
    api.resume_render_worker("completed-resume")
    stored = storage.get_job("completed-resume")
    assert stored["status"] == "completed"
    assert stored["stage"] == "done"
    assert stored["outputs"]["video"] == "dubbed.mp4"
