from fastapi.testclient import TestClient

from app import api, storage
from app.main import app


def test_health_voices_and_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health").json()["status"] == "ok"
        assert len(client.get("/api/voices").json()) == 2
        assert client.get("/api/jobs").json() == []
        assert client.get("/api/jobs/missing").status_code == 404
        assert client.post("/api/jobs", data={"source_lang": "fr"}).status_code == 422


def test_upload_persists_job_and_rejects_unsupported_options(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(api, "probe", lambda _: {"streams": [{"codec_type": "video"}]})
    monkeypatch.setattr(api.POOL, "submit", lambda *args, **kwargs: None)
    with TestClient(app) as client:
        response = client.post("/api/jobs", files={"file": ("sample.mp4", b"fake video", "video/mp4")})
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "queued"
        assert len(client.get("/api/jobs").json()) == 1
        assert client.delete(f"/api/jobs/{job_id}").json() == {"deleted": True}
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
        bad = client.post("/api/jobs", data={"voice": "unsupported"}, files={"file": ("x.mp4", b"x", "video/mp4")})
        assert bad.status_code == 400


def test_tts_preview_returns_synthesized_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")

    def fake_synth(text, voice, destination, duration, seed=1):
        destination.write_bytes(b"RIFF0000WAVE")
        return "edge-tts"

    monkeypatch.setattr("app.providers.synthesize_khmer", fake_synth)
    with TestClient(app) as client:
        response = client.post("/api/tts/preview", json={"voice": "km-KH-SreymomNeural"})
        assert response.status_code == 200, response.text
        assert response.headers["x-tts-provider"] == "edge-tts"
        assert response.content.startswith(b"RIFF")
        bad = client.post("/api/tts/preview", json={"voice": "unsupported"})
        assert bad.status_code == 400


def test_tts_preview_reports_tts_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.sqlite3")

    def failed_synth(*args, **kwargs):
        raise RuntimeError("TTS_UNAVAILABLE: Edge TTS -> Azure Speech (if configured) -> MMS failed (edge:down; mms:down)")

    monkeypatch.setattr("app.providers.synthesize_khmer", failed_synth)
    with TestClient(app) as client:
        response = client.post("/api/tts/preview", json={"voice": "km-KH-SreymomNeural"})
        assert response.status_code == 503, response.text
        assert "TTS_UNAVAILABLE" in response.json()["detail"]


def test_failure_error_preserves_tts_unavailable_code():
    from app.pipeline import failure_error

    payload = failure_error(RuntimeError("TTS_UNAVAILABLE: all providers failed"))
    assert payload["code"] == "TTS_UNAVAILABLE"
    assert "TTS_UNAVAILABLE" in payload["message_en"]
    assert payload["message_km"]
    assert failure_error(ValueError("no speech"))["code"] == "PROCESSING_FAILED"

