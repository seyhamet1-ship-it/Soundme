import re
import numpy as np
import pytest
from app.segments import normalize_khmer_text_for_speech, normalize_numbers


def test_mixed_language_punctuation_guard():
    assert normalize_khmer_text_for_speech("Today is good.") == "Today is good."
    assert normalize_khmer_text_for_speech("Today is good។") == "Today is good។"
    assert normalize_khmer_text_for_speech("Selim is here.") == "Selim is here."
    assert normalize_khmer_text_for_speech("សួស្តី.") == "សួស្តី។"
    assert normalize_khmer_text_for_speech("សួស្តី Selim។") == "សួស្តី Selim។"
    assert normalize_khmer_text_for_speech("ថ្ងៃនេះ Today is good។") == "ថ្ងៃនេះ Today is good។"


def test_technical_number_preservation():
    assert normalize_numbers("https://example.com/docs/test.pdf 2026") == "https://example.com/docs/test.pdf ២០២៦"
    assert normalize_numbers("file_report_2026.csv") == "file_report_2026.csv"
    assert normalize_numbers("API v2.0") == "API v2.0"
    assert normalize_numbers("2026 and 123") == "២០២៦ and ១២៣"


def test_khmer_text_normalization_punctuation_and_spaces():
    # 1. Punctuation conversion
    sample = "ទី១, ចូរចាក់ថាមពល; ទី២, ត្រូវភ្ជាប់ខ្សែ"
    norm = normalize_khmer_text_for_speech(sample)
    assert ";" not in norm
    assert "," not in norm
    assert "៖" in norm
    assert norm.endswith("។")

    # 2. Spaces before dependent vowels and diacritics removed
    bad_ortho = "ក ា រ ប ្រ ើ ប ្ រ ា ស់"  # artificial spaces inside words/coeng
    clean = normalize_khmer_text_for_speech(bad_ortho)
    assert " ា" not in clean
    assert " ្" not in clean
    assert " ើ" not in clean


def test_khmer_compound_bindings():
    # Test that common compounds are bound without staccato syllable spaces
    sample = "អរគុណអ្នក ទាំង អស់ គ្នា សូម ស្វាគមន៍ មក កាន់ វីដេអូ របស់ យើង ថ្ងៃ នេះ"
    norm = normalize_khmer_text_for_speech(sample)
    assert "អ្នកទាំងអស់គ្នា" in norm
    assert "សូមស្វាគមន៍" in norm
    assert "របស់យើង" in norm
    assert "ថ្ងៃនេះ" in norm


def test_question_intonation_preservation():
    # English source is a question; translation lacks '?'
    src = "How much does this computer cost?"
    km = "តើកុំព្យូទ័រនេះមានតម្លៃប៉ុន្មាន"
    norm = normalize_khmer_text_for_speech(km, source_text=src)
    assert norm.endswith("?")

    # English source with explicit '?'
    src2 = "Is this video available?"
    km2 = "តើវីដេអូនេះអាចមើលបានឬទេ."
    norm2 = normalize_khmer_text_for_speech(km2, source_text=src2)
    assert norm2.endswith("?")
    assert not norm2.endswith(".")


def test_exclamation_intonation_preservation():
    src = "Watch out!"
    km = "សូមប្រយ័ត្ន"
    norm = normalize_khmer_text_for_speech(km, source_text=src)
    assert norm.endswith("!")


def test_declarative_khmer_fullstop():
    src = "Today is a great day."
    km = "ថ្ងៃនេះគឺជាថ្ងៃដ៏ល្អ"
    norm = normalize_khmer_text_for_speech(km, source_text=src)
    assert norm.endswith("។")

    # ASCII period replaced by Khmer full stop
    km_with_dot = "ថ្ងៃនេះគឺជាថ្ងៃដ៏ល្អ."
    norm_dot = normalize_khmer_text_for_speech(km_with_dot, source_text=src)
    assert norm_dot.endswith("។")
    assert not norm_dot.endswith(".")


def test_mms_digit_translation():
    # In MMS-TTS, Khmer digits (០-៩) must translate to Arabic digits (0-9)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-khm")
    
    khmer_digits = "តម្លៃគឺ ១២០ ដុល្លារ"
    # Unprocessed Khmer digits yield zero digit tokens
    unprocessed_tokens = tokenizer(khmer_digits).input_ids
    # Normalized digits
    khmer_to_arabic = str.maketrans("០១២៣៤៥៦៧៨៩", "0123456789")
    processed_text = khmer_digits.translate(khmer_to_arabic)
    processed_tokens = tokenizer(processed_text).input_ids
    
    assert len(processed_tokens) > len(unprocessed_tokens)


def test_edge_tts_silenceremove_threshold():
    # Verify that EdgeTTSProvider uses the upgraded -50dB threshold to avoid clipping word tails
    import inspect
    from app.providers import EdgeTTSProvider
    src = inspect.getsource(EdgeTTSProvider.synthesize)
    assert "stop_threshold=-50dB" in src


def test_stream_render_loudness_consistency(tmp_path):
    # Verify stream_render_audio normalizes segment peak levels
    import wave
    from app.render import stream_render_audio
    
    rate = 48000
    # Create two segments: one very quiet (peak 0.15), one normal (peak 0.85)
    f1 = tmp_path / "fit-0000.wav"
    f2 = tmp_path / "fit-0001.wav"
    
    t = np.linspace(0, 1.0, rate, endpoint=False)
    sig_quiet = (0.15 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    sig_loud = (0.85 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    
    with wave.open(str(f1), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(sig_quiet.tobytes())
    with wave.open(str(f2), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(sig_loud.tobytes())
        
    fit_stats = [
        {"placement_start": 0.0, "fitted_duration": 1.0},
        {"placement_start": 1.5, "fitted_duration": 1.0},
    ]
    
    dest = tmp_path / "out.wav"
    dur = stream_render_audio(dest, 3.0, fit_stats, [f1, f2], rate=rate)
    assert dur == 3.0
    assert dest.exists()
    
    # Read output and check that the first segment was brought up in loudness
    with wave.open(str(dest), "rb") as w:
        out_data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        
    seg1_peak = np.max(np.abs(out_data[:rate]))
    seg2_peak = np.max(np.abs(out_data[int(1.5*rate):int(2.5*rate)]))
    
    # Both segments should now have strong, consistent broadcast peaks (> 0.6)
    assert seg1_peak > 0.6
    assert seg2_peak > 0.6
    assert abs(seg1_peak - seg2_peak) < 0.25
