from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass
class Segment:
    id: int
    start: float
    end: float
    source_text: str
    text_km: str = ""
    speaker: str | None = None


def merge_segments(raw: Iterable[object], *, min_duration: float = 1.5, max_duration: float = 12.0) -> list[Segment]:
    """Merge adjacent ASR chunks to sentence-like slots without exceeding max_duration."""
    out: list[Segment] = []
    for item in raw:
        text = str(getattr(item, "text", "")).strip()
        if not text:
            continue
        start = float(getattr(item, "start", -1.0))
        end = float(getattr(item, "end", -1.0))
        if end <= start:
            continue
        if out and start - out[-1].start <= max_duration and (out[-1].end - out[-1].start < min_duration or not re.search(r"[.!?。！？]$", out[-1].source_text)):
            prior = out[-1]
            combined = f"{prior.source_text} {text}".strip()
            if end - prior.start <= max_duration:
                prior.end, prior.source_text = end, combined
                continue
        out.append(Segment(len(out) + 1, start, end, text))
    return out


def normalize_numbers(text: str) -> str:
    """Convert common Arabic digits into Khmer digits for speech output."""
    return text.translate(str.maketrans("0123456789", "០១២៣៤៥៦៧៨៩"))


def fit_duration(source_duration: float, slot_duration: float) -> tuple[float, float]:
    """Return (tempo, overflow); never recommends cutting speech."""
    if source_duration <= 0 or slot_duration <= 0:
        raise ValueError("durations must be positive")
    tempo = min(1.25, max(1.0, source_duration / slot_duration))
    fitted = source_duration / tempo
    return tempo, max(0.0, fitted - slot_duration)


def schedule_clips(segments: list[Segment], durations: list[float], *, max_overlap: float = 0.1) -> list[tuple[int, float, float]]:
    """Validate timeline slots and return (id,start,end), allowing bounded silence overflow."""
    scheduled: list[tuple[int, float, float]] = []
    previous_end = 0.0
    for segment, duration in zip(segments, durations, strict=True):
        start = max(segment.start, previous_end - max_overlap)
        end = start + duration
        if scheduled and scheduled[-1][2] - start > max_overlap + 1e-6:
            raise ValueError("speech clips overlap by more than 100 ms")
        scheduled.append((segment.id, start, end))
        previous_end = end
    return scheduled


def _stamp(seconds: float) -> str:
    ms = round(max(0.0, seconds) * 1000)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{milli:03}"


def write_srt(segments: Iterable[Segment]) -> str:
    return "\n\n".join(f"{i}\n{_stamp(s.start)} --> {_stamp(s.end)}\n{s.text_km}" for i, s in enumerate(segments, 1)) + "\n"


def write_vtt(segments: Iterable[Segment]) -> str:
    def vttstamp(value: float) -> str:
        return _stamp(value).replace(",", ".")
    return "WEBVTT\n\n" + "\n\n".join(f"{vttstamp(s.start)} --> {vttstamp(s.end)}\n{s.text_km}" for s in segments) + "\n"


def is_khmer_text(text: str) -> bool:
    """Validate that NLLB output is genuine Khmer, not English passthrough or Chinese.

    Accepts Khmer with embedded Latin brand names/loanwords (e.g. "SoundMe").
    Rejects: empty text, English/other passthrough (zero Khmer letters),
    and any output containing Chinese characters (wrong target language).
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    khmer = sum("\u1780" <= c <= "\u17ff" for c in letters)
    if khmer == 0:
        return False
    return not any("\u4e00" <= c <= "\u9fff" for c in text)
