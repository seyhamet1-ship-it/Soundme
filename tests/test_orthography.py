"""Unit test for Khmer orthography sanity across all i18n strings."""
from __future__ import annotations
import pytest
from app.i18n import STRINGS, validate_khmer_orthography, validate_all_strings

def test_all_i18n_strings_pass_orthography():
    violations = validate_all_strings()
    assert not violations, f"Orthography violations found: {violations}"

def test_coeng_requires_consonant():
    # Coeng followed by digit is invalid
    bad = "មិន\u17D2១"
    violations = validate_khmer_orthography(bad, "bad_coeng")
    assert any("Coeng" in v for v in violations)

def test_no_leading_dependent_vowel():
    # Dependent vowel \u17B6 at start of word
    bad = "\u17B6ក"
    violations = validate_khmer_orthography(bad, "bad_vowel")
    assert any("dependent vowel" in v for v in violations)

def test_no_invisible_characters():
    # ZWSP \u200B
    bad = "សួស្តី\u200Bកម្ពុជា"
    violations = validate_khmer_orthography(bad, "bad_invisible")
    assert any("invisible" in v for v in violations)

def test_correct_tts_unavailable_spelling():
    # Verify the specific spelling mentioned in the prompt:
    # "មិនអាចបង្កើតសំឡេងខ្មែរបាន" must be present and correctly spelled
    tts_unavail = STRINGS["tts_unavailable"].km
    assert "មិនអាចបង្កើតសំឡេងខ្មែរបាន" in tts_unavail
    # The old misspelled word "មិនអាប្កើត" (with ប្ក instead of អាចបង្កើត) must NEVER appear
    misspelled = "\u1798\u17b7\u1793\u17a2\u17b6\u1794\u17d2\u1780\u17be\u178f"
    assert misspelled not in tts_unavail
