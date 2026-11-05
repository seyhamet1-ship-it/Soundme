import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app import storage, api
from app.config import UPLOAD_DIR

AUTH_USER1 = {"Authorization": "Bearer token-user1", "X-Access-Token": "token-user1"}
AUTH_USER2 = {"Authorization": "Bearer token-user2", "X-Access-Token": "token-user2"}


@pytest.fixture
def mock_env(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite3"
    uploads_path = tmp_path / "uploads"
    uploads_path.mkdir(parents=True, exist_ok=True)
    
    monkeypatch.setenv("ACCESS_TOKENS", "token-user1,token-user2,soundme-test-token")
    monkeypatch.setenv("WARM_MODELS_AT_STARTUP", "0")
    monkeypatch.setattr(storage, "DB", db_path)
    monkeypatch.setattr(api, "UPLOADS", uploads_path)
    monkeypatch.setattr(api, "UPLOAD_DIR", uploads_path)
    
    return {"tmp": tmp_path, "uploads": uploads_path}


def create_test_job(jid: str, status: str, user_token: str, uploads_dir: Path, files: bool = True):
    from app.storage import set_job, _token_to_user_id
    user_id = _token_to_user_id(user_token)
    
    job_dir = uploads_dir / jid
    if files:
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "source.mp4").write_bytes(b"video-data")
        (job_dir / "voice-0000.wav").write_bytes(b"voice-data")
        (job_dir / "fit-0000.wav").write_bytes(b"fit-data")
        (job_dir / "dub.wav").write_bytes(b"dub-data")
        (job_dir / "dubbed.mp4").write_bytes(b"dubbed-data")
        (job_dir / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
        (job_dir / "asr_checkpoint.json").write_text("{}", encoding="utf-8")

    payload = {
        "id": jid,
        "filename": f"test_{jid}.mp4",
        "status": status,
        "stage": "done" if status == "completed" else status,
        "progress": 100 if status == "completed" else (50 if status == "running" else 0),
        "source_path": str(job_dir / "source.mp4"),
        "owner_token": user_token,
        "user_id": user_id,
        "outputs": {
            "video": f"{jid}/dubbed.mp4",
            "audio": f"{jid}/dub.wav",
            "srt": f"{jid}/subtitles.srt"
        } if status == "completed" else {}
    }
    set_job(jid, payload)
    return payload


def test_delete_completed_job(mock_env):
    with TestClient(app) as client:
        jid = "a1b2c3d4e5f60001"
        create_test_job(jid, "completed", "token-user1", mock_env["uploads"])
        
        # Verify job exists
        assert client.get(f"/api/jobs/{jid}", headers=AUTH_USER1).status_code == 200
        assert (mock_env["uploads"] / jid).exists()
        
        # Delete job
        res = client.delete(f"/api/jobs/{jid}", headers=AUTH_USER1)
        assert res.status_code == 200
        assert res.json() == {"deleted": True}
        
        # Verify job is gone from DB and disk
        assert client.get(f"/api/jobs/{jid}", headers=AUTH_USER1).status_code == 404
        assert not (mock_env["uploads"] / jid).exists()


def test_delete_failed_and_canceled_jobs(mock_env):
    with TestClient(app) as client:
        # Failed job
        jid_fail = "a1b2c3d4e5f60002"
        create_test_job(jid_fail, "failed", "token-user1", mock_env["uploads"])
        assert client.delete(f"/api/jobs/{jid_fail}", headers=AUTH_USER1).status_code == 200
        assert client.get(f"/api/jobs/{jid_fail}", headers=AUTH_USER1).status_code == 404
        assert not (mock_env["uploads"] / jid_fail).exists()

        # Canceled job
        jid_canc = "a1b2c3d4e5f60003"
        create_test_job(jid_canc, "canceled", "token-user1", mock_env["uploads"])
        assert client.delete(f"/api/jobs/{jid_canc}", headers=AUTH_USER1).status_code == 200
        assert client.get(f"/api/jobs/{jid_canc}", headers=AUTH_USER1).status_code == 404
        assert not (mock_env["uploads"] / jid_canc).exists()


def test_cannot_delete_running_job(mock_env):
    with TestClient(app) as client:
        jid = "a1b2c3d4e5f60004"
        create_test_job(jid, "running", "token-user1", mock_env["uploads"])
        
        res = client.delete(f"/api/jobs/{jid}", headers=AUTH_USER1)
        assert res.status_code == 409
        err = res.json()
        assert err["detail"]["code"] == "JOB_ACTIVE"
        
        # Job must still exist
        assert client.get(f"/api/jobs/{jid}", headers=AUTH_USER1).status_code == 200
        assert (mock_env["uploads"] / jid).exists()


def test_user_isolation_on_deletion(mock_env):
    with TestClient(app) as client:
        jid = "a1b2c3d4e5f60005"
        create_test_job(jid, "completed", "token-user1", mock_env["uploads"])
        
        # User 2 tries to delete User 1's job
        res = client.delete(f"/api/jobs/{jid}", headers=AUTH_USER2)
        assert res.status_code == 404
        
        # Job still intact
        assert client.get(f"/api/jobs/{jid}", headers=AUTH_USER1).status_code == 200
        assert (mock_env["uploads"] / jid).exists()


def test_path_traversal_rejected(mock_env):
    with TestClient(app) as client:
        # Invalid job IDs containing traversal dots or slashes
        assert client.delete("/api/jobs/..", headers=AUTH_USER1).status_code in {400, 404, 405}
        assert client.delete("/api/jobs/../../uploads", headers=AUTH_USER1).status_code in {400, 404, 405}
        assert client.delete("/api/jobs/not-a-valid-hex-id-with-special!@#", headers=AUTH_USER1).status_code in {400, 404}


def test_bulk_delete_specific_jobs(mock_env):
    with TestClient(app) as client:
        j1 = "a1b2c3d4e5f60011"
        j2 = "a1b2c3d4e5f60012"
        j3 = "a1b2c3d4e5f60013"
        create_test_job(j1, "completed", "token-user1", mock_env["uploads"])
        create_test_job(j2, "failed", "token-user1", mock_env["uploads"])
        create_test_job(j3, "completed", "token-user1", mock_env["uploads"])
        
        # Delete j1 and j2
        res = client.post("/api/jobs/bulk-delete", json={"job_ids": [j1, j2]}, headers=AUTH_USER1)
        assert res.status_code == 200
        data = res.json()
        assert set(data["deleted"]) == {j1, j2}
        assert data["count"] == 2
        assert len(data["failed"]) == 0
        
        # j1 and j2 gone; j3 remains
        assert client.get(f"/api/jobs/{j1}", headers=AUTH_USER1).status_code == 404
        assert client.get(f"/api/jobs/{j2}", headers=AUTH_USER1).status_code == 404
        assert client.get(f"/api/jobs/{j3}", headers=AUTH_USER1).status_code == 200


def test_bulk_delete_filters_completed_and_failed(mock_env):
    with TestClient(app) as client:
        c1 = "a1b2c3d4e5f60021"
        c2 = "a1b2c3d4e5f60022"
        f1 = "a1b2c3d4e5f60023"
        r1 = "a1b2c3d4e5f60024"
        create_test_job(c1, "completed", "token-user1", mock_env["uploads"])
        create_test_job(c2, "completed", "token-user1", mock_env["uploads"])
        create_test_job(f1, "failed", "token-user1", mock_env["uploads"])
        create_test_job(r1, "running", "token-user1", mock_env["uploads"])
        
        # Clear completed
        res = client.post("/api/jobs/bulk-delete", json={"filter": "completed"}, headers=AUTH_USER1)
        assert res.status_code == 200
        data = res.json()
        assert set(data["deleted"]) == {c1, c2}
        assert data["count"] == 2
        
        # c1 and c2 gone; f1 and r1 remain
        assert client.get(f"/api/jobs/{c1}", headers=AUTH_USER1).status_code == 404
        assert client.get(f"/api/jobs/{c2}", headers=AUTH_USER1).status_code == 404
        assert client.get(f"/api/jobs/{f1}", headers=AUTH_USER1).status_code == 200
        assert client.get(f"/api/jobs/{r1}", headers=AUTH_USER1).status_code == 200
        
        # Clear failed
        res2 = client.post("/api/jobs/bulk-delete", json={"filter": "failed"}, headers=AUTH_USER1)
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["deleted"] == [f1]
        
        # Running job must still be running and untouched!
        assert client.get(f"/api/jobs/{r1}", headers=AUTH_USER1).status_code == 200


def test_bulk_delete_partial_failure(mock_env):
    with TestClient(app) as client:
        valid = "a1b2c3d4e5f60031"
        running = "a1b2c3d4e5f60032"
        nonexistent = "a1b2c3d4e5f60033"
        create_test_job(valid, "completed", "token-user1", mock_env["uploads"])
        create_test_job(running, "running", "token-user1", mock_env["uploads"])
        
        res = client.post("/api/jobs/bulk-delete", json={"job_ids": [valid, running, nonexistent]}, headers=AUTH_USER1)
        assert res.status_code == 200
        data = res.json()
        assert data["deleted"] == [valid]
        assert data["count"] == 1
        assert len(data["failed"]) == 2
        
        failed_reasons = {f["id"]: f["reason"] for f in data["failed"]}
        assert failed_reasons[running] == "job_running"
        assert failed_reasons[nonexistent] == "not_found"


def test_missing_files_handled_gracefully(mock_env):
    with TestClient(app) as client:
        jid = "a1b2c3d4e5f60041"
        # Job registered in DB but files do not exist on disk
        create_test_job(jid, "completed", "token-user1", mock_env["uploads"], files=False)
        assert not (mock_env["uploads"] / jid).exists()
        
        # Deletion succeeds smoothly
        res = client.delete(f"/api/jobs/{jid}", headers=AUTH_USER1)
        assert res.status_code == 200
        assert client.get(f"/api/jobs/{jid}", headers=AUTH_USER1).status_code == 404
