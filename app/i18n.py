"""Central internationalization (i18n) registry for Soundme.

Single source of truth for all Khmer and English UI messages, error strings,
and speech fixtures. Contains orthography validation rules to prevent
spelling and encoding regressions.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import NamedTuple

class I18nEntry(NamedTuple):
    km: str
    en: str

# Master dictionary: all user-facing strings referenced by key
STRINGS: dict[str, I18nEntry] = {
    # App Header & Meta
    "app_title": I18nEntry(
        km="កម្មវិធីបកសំឡេងវីដេអូជាភាសាខ្មែរ",
        en="Khmer Video Dubber",
    ),
    "app_subtitle": I18nEntry(
        km="បកប្រែសំឡេងចិន ឬអង់គ្លេស ទៅជាភាសាខ្មែរ",
        en="Translate Chinese or English speech to Khmer",
    ),
    
    # Upload Card
    "upload_heading": I18nEntry(
        km="ជ្រើសរើស ឬអូសវីដេអូមកទីនេះ",
        en="Choose or drop a video",
    ),
    "source_lang": I18nEntry(
        km="ភាសាដើម",
        en="Source language",
    ),
    "khmer_voice": I18nEntry(
        km="សំឡេងខ្មែរ",
        en="Khmer voice",
    ),
    "voice_sreymom": I18nEntry(
        km="ស្រីមុំ · Sreymom",
        en="Sreymom · Female",
    ),
    "voice_piseth": I18nEntry(
        km="ពិសិដ្ឋ · Piseth",
        en="Piseth · Male",
    ),
    "preview_voice_btn": I18nEntry(
        km="▶ សាកល្បងសំឡេង",
        en="▶ Preview voice",
    ),
    "keep_background": I18nEntry(
        km="រក្សាសំឡេងផ្ទៃក្រោយ",
        en="Keep background sound",
    ),
    "burn_subtitles": I18nEntry(
        km="បង្កប់ចំណងជើងរង",
        en="Burn subtitles",
    ),
    "review_before_tts": I18nEntry(
        km="ពិនិត្យការបកប្រែមុន",
        en="Review translation before voiceover",
    ),
    "ownership_notice": I18nEntry(
        km="សូមបង្ហោះតែមាតិកាដែលអ្នកជាម្ចាស់ ឬមានសិទ្ធិប្រើប្រាស់។",
        en="Upload content you have permission to use.",
    ),
    "start_dubbing": I18nEntry(
        km="ចាប់ផ្តើមបកសំឡេង",
        en="Start dubbing",
    ),
    
    # Processing Card
    "processing_heading": I18nEntry(
        km="ដំណើរការ · Processing",
        en="Processing",
    ),
    "stage_waiting": I18nEntry(
        km="រង់ចាំវីដេអូ",
        en="Waiting for video",
    ),
    "cancel_btn": I18nEntry(
        km="បោះបង់",
        en="Cancel",
    ),
    
    # Review Card
    "review_heading": I18nEntry(
        km="ពិនិត្យការបកប្រែ",
        en="Review translation",
    ),
    "save_and_continue": I18nEntry(
        km="រក្សាទុក ហើយបន្ត",
        en="Save and continue",
    ),
    
    # History Card
    "job_history": I18nEntry(
        km="ប្រវត្តិការងារ",
        en="Recent jobs",
    ),
    "history_empty": I18nEntry(
        km="មិនទាន់មានការងារទេ។",
        en="No jobs yet.",
    ),
    
    # Error Messages
    "tts_unavailable": I18nEntry(
        km="មិនអាចបង្កើតសំឡេងខ្មែរបានទេ។ សូមសាកល្បងម្ដងទៀត។",
        en="Unable to synthesize Khmer voice. Please try again.",
    ),
    "processing_failed": I18nEntry(
        km="ដំណើរការមិនបានសម្រេច។ សូមពិនិត្យវីដេអូ ហើយសាកល្បងម្ដងទៀត។",
        en="Processing failed. Please check the video and try again.",
    ),
    "server_restarted": I18nEntry(
        km="ម៉ាស៊ីនមេបានចាប់ផ្ដើមឡើងវិញ។",
        en="Job interrupted by server restart.",
    ),
    "tts_empty_text": I18nEntry(
        km="គ្មានអត្ថបទសម្រាប់បង្កើតសំឡេងទេ។",
        en="No speakable text provided for synthesis.",
    ),
    "no_speech_detected": I18nEntry(
        km="រកមិនឃើញការនិយាយនៅក្នុងវីដេអូទេ។ សូមសាកល្បងវីដេអូដែលមានសំឡេងនិយាយច្បាស់។",
        en="No speech was detected. Try a video with audible speech.",
    ),
    "no_audio_track": I18nEntry(
        km="វីដេអូនេះគ្មានសំឡេងសម្រាប់បកប្រែទេ។",
        en="This video has no audio track to transcribe.",
    ),
    "no_video_stream": I18nEntry(
        km="ឯកសារដែលបានបង្ហោះគ្មានរូបភាពវីដេអូទេ។",
        en="The uploaded file has no video stream.",
    ),
    "file_empty": I18nEntry(
        km="ឯកសារទទេ។ សូមជ្រើសរើសវីដេអូត្រឹមត្រូវ។",
        en="File is empty. Please choose a valid video.",
    ),
    "upload_too_large": I18nEntry(
        km="ទំហំឯកសារលើសពីកម្រិតកំណត់ 4096 MB។",
        en="File exceeds the 4096 MB upload limit.",
    ),
    "invalid_video": I18nEntry(
        km="ទ្រង់ទ្រាយវីដេអូមិនត្រឹមត្រូវ ឬខូច។",
        en="Invalid or corrupted video format.",
    ),
    "duration_too_long": I18nEntry(
        km="រយៈពេលវីដេអូលើសពីកម្រិតកំណត់។",
        en="Video duration exceeds the configured limit.",
    ),
    "disk_space_low": I18nEntry(
        km="ទំហំថាសមិនគ្រប់គ្រាន់សម្រាប់ដំណើរការវីដេអូនេះ។",
        en="Insufficient disk space to process this video.",
    ),
    "long_job_eta": I18nEntry(
        km="ការដំណើរការវីដេអូវែងអាចចំណាយពេលច្រើន។ សូមកុំបិទកម្មវិធី។",
        en="Long video processing may take a while. Please keep the app running.",
    ),

    # Speech Fixture & Probes
    "default_preview_text": I18nEntry(
        km="សួស្តី។ នេះជាការសាកល្បងសំឡេង។",
        en="Hello. This is a voice test.",
    ),
    "demo_fixture_hello": I18nEntry(
        km="សួស្តីអ្នកទាំងអស់គ្នា សូមស្វាគមន៍មកកាន់ឆានែលរបស់យើង។",
        en="Hello everyone, welcome to our channel.",
    ),
    "demo_fixture_product": I18nEntry(
        km="ថ្ងៃនេះយើងនឹងណែនាំផលិតផលថ្មីមួយ។",
        en="Today we will introduce a new product.",
    ),
    "demo_fixture_price_usd": I18nEntry(
        km="តម្លៃគឺមួយរយម្ភៃដុល្លារ។",
        en="The price is one hundred and twenty dollars.",
    ),
    "demo_fixture_price_cny": I18nEntry(
        km="តម្លៃគឺមួយរយម្ភៃយន់។",
        en="The price is one hundred and twenty yuan.",
    ),
    "demo_review_prefix": I18nEntry(
        km="សូមពិនិត្យ៖ ",
        en="Please review: ",
    ),
    "demo_review_check": I18nEntry(
        km="សូមពិនិត្យការបកប្រែនេះ",
        en="Please review this translation",
    ),
}

# Khmer Unicode Constants for Orthography Sanity
KHMER_CONSONANTS = set(chr(c) for c in range(0x1780, 0x17A3))
KHMER_INDEPENDENT_VOWELS = set(chr(c) for c in range(0x17A3, 0x17B4))
KHMER_SUBJOINABLE = KHMER_CONSONANTS | KHMER_INDEPENDENT_VOWELS
KHMER_DEPENDENT_VOWELS = set(chr(c) for c in range(0x17B6, 0x17C6))
COENG = "\u17D2"
FORBIDDEN_INVISIBLE = {
    "\u200B": "Zero Width Space (ZWSP)",
    "\u200C": "Zero Width Non-Joiner (ZWNJ)",
    "\u200D": "Zero Width Joiner (ZWJ)",
    "\uFEFF": "Byte Order Mark (BOM)",
    "\u00A0": "Non-Breaking Space (NBSP)",
    "\u2028": "Line Separator",
    "\u2029": "Paragraph Separator",
}

def validate_khmer_orthography(text: str, key: str = "") -> list[str]:
    """Verify Khmer text adheres to standard orthography rules:
    1. Coeng (U+17D2) must be followed by a subjoinable consonant or independent vowel.
    2. No word or syllable can begin with a dependent vowel (U+17B6 - U+17C5).
    3. No invisible or anomalous characters (ZWSP, ZWNJ, ZWJ, BOM, etc.).
    """
    violations: list[str] = []
    
    # Rule 1: Coeng check
    for idx, char in enumerate(text):
        if char == COENG:
            if idx + 1 >= len(text):
                violations.append(f"[{key}] Coeng U+17D2 at end of string in {text!r}")
            elif text[idx + 1] not in KHMER_SUBJOINABLE:
                follower = text[idx + 1]
                violations.append(
                    f"[{key}] Coeng U+17D2 followed by invalid character U+{ord(follower):04X} ({follower}) in {text!r}"
                )

    # Rule 2: No leading dependent vowel
    tokens = re.split(r"[\s\.,·:!?()\-—]+", text)
    for tok in tokens:
        if tok and tok[0] in KHMER_DEPENDENT_VOWELS:
            violations.append(
                f"[{key}] Token '{tok}' starts with leading dependent vowel U+{ord(tok[0]):04X} in {text!r}"
            )

    # Rule 3: No invisible characters
    for idx, char in enumerate(text):
        if char in FORBIDDEN_INVISIBLE:
            name = FORBIDDEN_INVISIBLE[char]
            violations.append(
                f"[{key}] Contains forbidden invisible character {name} (U+{ord(char):04X}) at index {idx} in {text!r}"
            )

    return violations

def validate_all_strings() -> list[str]:
    """Run orthography sanity on every registered string in STRINGS."""
    all_violations: list[str] = []
    for key, entry in STRINGS.items():
        all_violations.extend(validate_khmer_orthography(entry.km, key))
    return all_violations

def get_km(key: str) -> str:
    return STRINGS[key].km

def get_en(key: str) -> str:
    return STRINGS[key].en

def export_to_json(target_path: Path | None = None) -> Path:
    """Export strings as JSON for the frontend."""
    if target_path is None:
        target_path = Path(__file__).resolve().parents[1] / "frontend" / "src" / "i18n" / "strings.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        key: {"km": entry.km, "en": entry.en}
        for key, entry in STRINGS.items()
    }
    target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target_path

def export_to_markdown(target_path: Path | None = None) -> Path:
    """Export strings as docs/khmer_strings_review.md for proofreading."""
    if target_path is None:
        target_path = Path(__file__).resolve().parents[1] / "docs" / "khmer_strings_review.md"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Khmer Strings Review",
        "",
        "This table contains all user-facing strings, error messages, and fixtures",
        "centralized in `app/i18n.py` and exported to `frontend/src/i18n/strings.json`.",
        "",
        "| Key | Khmer | English |",
        "|-----|-------|---------|",
    ]
    for key, entry in STRINGS.items():
        # Sanitize pipe symbols in table markdown if any
        km_esc = entry.km.replace("|", "\\|")
        en_esc = entry.en.replace("|", "\\|")
        lines.append(f"| `{key}` | {km_esc} | {en_esc} |")
    lines.append("")
    target_path.write_text("\n".join(lines), encoding="utf-8")
    return target_path

if __name__ == "__main__":
    violations = validate_all_strings()
    if violations:
        print(f"FAILED: {len(violations)} orthography violations found:")
        for v in violations:
            print(f"  {v}")
        raise SystemExit(1)
    json_path = export_to_json()
    md_path = export_to_markdown()
    print(f"SUCCESS: {len(STRINGS)} strings verified with zero orthography errors.")
    print(f"Exported JSON    : {json_path}")
    print(f"Exported Markdown: {md_path}")
