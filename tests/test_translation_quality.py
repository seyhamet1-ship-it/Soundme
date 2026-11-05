"""
Tests for translation quality validation, mixed Chinese/Khmer handling,
controlled correction, review gates, and resume behavior.
"""

import pytest
from app.segments import (
    validate_translation_text,
    attempt_translation_correction,
    is_khmer_text,
    is_passthrough_text,
    Segment,
)


def test_pure_khmer_text():
    """Verify that genuine Khmer text is validated as valid."""
    text = "កូន នៅ ថ្ងៃ ដំបូង នៃ ការ រៀន ខ្ញុំ ប្រាប់ អ្នក ឲ្យ មើល"
    is_val, reason = validate_translation_text(text, "First day of school")
    assert is_val is True
    assert reason == "valid_khmer"
    assert is_khmer_text(text) is True


def test_khmer_text_with_accidental_chinese():
    """Verify that Khmer text containing accidental Chinese characters is rejected."""
    bad_text = "ការទទួលទានរង្វាន់រង្វាន់ រង្វាន់របស់រៀលរៀ尔ីង"
    is_val, reason = validate_translation_text(bad_text, "source text")
    assert is_val is False
    assert reason == "contains_chinese"
    assert is_khmer_text(bad_text) is False


def test_legitimate_proper_names_and_loanwords():
    """Verify that legitimate Latin loanwords/brand names embedded in Khmer are accepted."""
    mixed_valid = "ខ្ញុំមានរថយន្ត BMW និង Bugatti Veyron មួយគ្រឿង"
    is_val, reason = validate_translation_text(mixed_valid, "I have a BMW and Bugatti Veyron")
    assert is_val is True
    assert reason == "valid_khmer"

    # Latin proper name passthrough
    passthrough = "Selim"
    is_val, reason = validate_translation_text(passthrough, "Selim")
    assert is_val is True
    assert reason == "valid_passthrough"


def test_numbers_latin_abbreviations_and_punctuation():
    """Verify numbers, Latin abbreviations, and Khmer punctuation."""
    # Khmer numerals and ASCII numbers
    num_text = "១២៣ ៤៥៦ 789 2026"
    is_val, reason = validate_translation_text(num_text, "123 456 789 2026")
    assert is_val is True

    # Punctuation and abbreviations
    punct_text = "SoundMe AI: កំណែថ្មី ១.០ ។"
    is_val, reason = validate_translation_text(punct_text, "SoundMe AI: New version 1.0.")
    assert is_val is True
    assert reason == "valid_khmer"


def test_empty_and_invalid_translations():
    """Verify that empty, whitespace, and non-Khmer text without match are rejected."""
    # Empty
    is_val, reason = validate_translation_text("", "Some text")
    assert is_val is False
    assert reason == "empty"

    is_val, reason = validate_translation_text("   ", "Some text")
    assert is_val is False
    assert reason == "empty"

    # Pure English when source was Chinese (wrong language target)
    is_val, reason = validate_translation_text("Hello my friend", "你好我的朋友")
    assert is_val is False
    assert reason == "non_khmer"

    # Script hallucination (Arabic/Cyrillic embedded in Khmer)
    hallucination = "ទទួលទានរៀលរៀلរៀលរៀлីង"
    is_val, reason = validate_translation_text(hallucination, "source")
    assert is_val is False
    assert reason == "script_hallucination"


def test_controlled_correction_chinese_degeneration():
    """Verify that attempt_translation_correction successfully produces clean Khmer."""
    source = "接受王清雪的奶茶並喝下獎勵兵王稱號選擇二接受上官前的奶茶並喝下獎勵頂級跑車不加迪威龍一輛兵王能力不及迪威龍這讓我怎麼選老大兩個笑話兩杯奶茶你到底選誰這個問題小孩子才做選擇我全都要確定你胃口還挺大叮"
    bad_km = "ការទទួលទានរង្វាន់រង្វាន់ រង្វាន់របស់រៀលរៀ尔ីង"

    corrected, status = attempt_translation_correction(source, bad_km, "zh")
    assert status == "corrected"
    assert corrected is not None
    # Must not contain any Chinese characters
    assert not any("\u4e00" <= c <= "\u9fff" for c in corrected)
    # Must be valid Khmer
    is_val, reason = validate_translation_text(corrected, source, "zh")
    assert is_val is True
    assert reason == "valid_khmer"


def test_unresolvable_translation_review_gate(tmp_path, monkeypatch):
    """Verify that when a segment has unresolvable translation defects,
    the pipeline safely pauses in awaiting_review with REVIEW_REQUIRED rather than crashing to failed."""
    from app.storage import set_job, get_job
    monkeypatch.setattr("app.storage.DB", tmp_path / "test_review_gate.sqlite3")

    job_id = "test_review_gate_job_001"
    set_job(job_id, {
        "id": job_id,
        "status": "running",
        "stage": "translate",
        "progress": 38,
        "user_id": "desktop:local",
        "options": {"source_lang": "zh", "review_before_tts": False},
    })

    # Call render with a bad segment that cannot be corrected (e.g. empty source)
    from app.render import render
    bad_segment = Segment(id=1, start=0.0, end=3.0, source_text="", text_km="Invalid 尔 text")

    render_called = False
    try:
        render(
            job_id,
            source=tmp_path / "dummy.mp4",
            root=tmp_path,
            segments=[bad_segment],
            duration=3.0,
            opts={"source_lang": "zh"},
            transcript=type("Transcript", (), {"language": "zh", "language_probability": 1.0})(),
            providers=["nllb"],
            video_stream={"width": 1280, "height": 720},
            started=1000.0,
        )
    except Exception as exc:
        render_called = True
        pytest.fail(f"render() should pause gracefully in awaiting_review, not raise {exc}")

    # Check job state in storage
    job = get_job(job_id)
    assert job is not None
    assert job["status"] == "awaiting_review"
    assert job["stage"] == "review"
    assert job["error"]["code"] == "REVIEW_REQUIRED"
    assert "timing_review" in job
    assert job["timing_review"]["review_type"] == "translation"


def test_tts_fallback_and_resume_after_segment_correction(tmp_path, monkeypatch):
    """Verify that an awaiting_review job can be resumed with corrected segments
    without repeating ASR or translation."""
    from app.storage import set_job, get_job, update_job
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr("app.storage.DB", tmp_path / "test_resume.sqlite3")
    monkeypatch.setenv("DESKTOP_LOCAL_MODE", "1")

    job_id = "test_resume_job_002"
    source_file = tmp_path / "test_video.mp4"
    source_file.write_bytes(b"FAKE_VIDEO_CONTENT")

    # Initial job in awaiting_review with corrected segments
    set_job(job_id, {
        "id": job_id,
        "status": "awaiting_review",
        "stage": "review",
        "progress": 50,
        "user_id": "desktop:local",
        "source_path": str(source_file),
        "detected_language": "zh",
        "options": {"source_lang": "zh", "voice": "km-KH-SreymomNeural"},
        "segments": [
            {"id": 1, "start": 0.0, "end": 2.0, "source_text": "你好", "text_km": "សួស្តី"}
        ],
        "providers": ["nllb"],
    })

    client = TestClient(app, base_url="http://127.0.0.1")

    # Step 1: Update segments via PUT /api/jobs/{id}/segments
    corrected_segments = [
        {"id": 1, "start": 0.0, "end": 2.0, "source_text": "你好", "text_km": "ជំរាបសួរ"}
    ]
    put_res = client.put(f"/api/jobs/{job_id}/segments", json=corrected_segments)
    assert put_res.status_code == 200

    updated_job = get_job(job_id)
    assert updated_job["segments"][0]["text_km"] == "ជំរាបសួរ"

    # Step 2: Resume via POST /api/jobs/{id}/resume
    with patch("app.api.POOL.submit") as mock_submit, \
         patch("app.api.probe") as mock_probe:
        mock_probe.return_value = {
            "streams": [{"codec_type": "video"}],
            "format": {"duration": "2.0"}
        }

        resume_res = client.post(f"/api/jobs/{job_id}/resume")
        assert resume_res.status_code == 200
        assert resume_res.json()["status"] == "running"
        assert mock_submit.called
