from __future__ import annotations

import re
import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


@dataclass
class Segment:
    id: int
    start: float
    end: float
    source_text: str
    text_km: str = ""
    speaker: str | None = None


def merge_segments(
    raw: Iterable[object], *, min_duration: float = 1.5,
    max_duration: float = 12.0, max_gap: float = 0.35,
) -> list[Segment]:
    """Merge only contiguous, unfinished ASR sentence fragments.

    ASR chunks are not necessarily sentence boundaries.  A short gap alone is
    not enough to merge them: a terminal punctuation mark keeps two sentences
    separate, preserving their source timing and pause.
    """
    out: list[Segment] = []
    for item in raw:
        text = str(getattr(item, "text", "")).strip()
        if not text:
            continue
        start = float(getattr(item, "start", -1.0))
        end = float(getattr(item, "end", -1.0))
        if end <= start:
            continue
        if out:
            prior = out[-1]
            gap = start - prior.end
            prior_ends_sentence = bool(re.search(r"(?<!\.)[.!?。！？]$", prior.source_text))
            combined = f"{prior.source_text} {text}".strip()
            # ``min_duration`` remains part of the public signature for
            # callers that tune ASR chunking; sentence boundaries and a small
            # actual gap are the safety conditions for a merge.
            if (
                -max_gap <= gap <= max_gap
                and not prior_ends_sentence
                and end - prior.start <= max_duration
            ):
                prior.end = max(prior.end, end)
                prior.source_text = combined
                continue
            if start < prior.end:
                start = prior.end
                if end <= start:
                    continue
        out.append(Segment(len(out) + 1, start, end, text))
    return out


def normalize_numbers(text: str) -> str:
    """Convert common Arabic digits into Khmer digits for speech output."""
    return text.translate(str.maketrans("0123456789", "០១២៣៤៥៦៧៨៩"))


def fit_duration(source_duration: float, slot_duration: float, *, max_tempo: float = 1.15) -> tuple[float, float]:
    """Return (tempo, overflow); never recommends cutting speech."""
    if not all(math.isfinite(value) and value > 0 for value in (source_duration, slot_duration, max_tempo)):
        raise ValueError("durations must be positive")
    if max_tempo < 1.0:
        raise ValueError("max_tempo must be at least 1.0")
    tempo = min(max_tempo, max(1.0, source_duration / slot_duration))
    fitted = source_duration / tempo
    return tempo, max(0.0, fitted - slot_duration)


def plan_timing(
    segments: Sequence[Segment],
    raw_durations: Sequence[float],
    *,
    max_tempo: float = 1.15,
    allow_gap_recovery: bool = False,
    preferred_tempo: float = 1.25,
    max_gap_borrow: float = 0.85,
    min_pause: float = 0.15,
    total_duration: float | None = None,
) -> list[dict[str, Any]]:
    """Return a deterministic speech placement plan with optional automatic timing recovery.

    When allow_gap_recovery is False (default), inter-segment pauses are not
    consumed and any speech exceeding max_tempo is flagged for review.

    When allow_gap_recovery is True, an automatic recovery ladder is applied:
    1. Exact / tempo fit within preferred tempo (default 1.25x).
    2. Safe redistribution into the inter-segment pause preserving breath pauses.
    3. Scaled tempo up to max_tempo (default 2.0x) combined with safe gap borrowing.
    4. Only flag review if speech cannot fit without colliding into the next speaker.
    """
    if len(segments) != len(raw_durations):
        raise ValueError("each segment requires one synthesized duration")
    if not math.isfinite(max_tempo) or max_tempo < 1.0:
        raise ValueError("max_tempo must be finite and at least 1.0")

    plan: list[dict[str, Any]] = []
    previous_placement_end: float | None = None
    for index, (segment, raw_duration) in enumerate(zip(segments, raw_durations, strict=True)):
        start, end, raw = float(segment.start), float(segment.end), float(raw_duration)
        if not all(math.isfinite(value) for value in (start, end, raw)):
            raise ValueError(f"segment {segment.id} has non-finite timing")
        if start < 0 or end <= start:
            raise ValueError(f"segment {segment.id} has invalid source timestamps")
        if raw <= 0:
            raise ValueError(f"segment {segment.id} has invalid synthesized duration")

        target = end - start
        required_tempo = max(1.0, raw / target)

        available_gap = 0.0
        if index + 1 < len(segments):
            next_start = float(segments[index + 1].start)
            if not math.isfinite(next_start):
                raise ValueError(f"segment {segments[index + 1].id} has non-finite timing")
            available_gap = max(0.0, next_start - end)

        if not allow_gap_recovery:
            tempo = min(required_tempo, max_tempo)
            fitted = raw / tempo
            overflow = max(0.0, fitted - target)
            gap_used = 0.0
            recovery_action = "tempo_fit" if overflow <= 1e-9 else "review_required"
            placement_start = start
            placement_end = placement_start + fitted
            overlap = previous_placement_end is not None and placement_start < previous_placement_end - 1e-9
            review_required = overflow > 1e-9 or overlap
            review_reason = "source_overlap" if overlap else ("tempo_limit" if overflow > 1e-9 else "")
        else:
            usable_gap = max(0.0, min(available_gap * max_gap_borrow, available_gap - min_pause))
            if raw <= target:
                tempo = 1.0
                fitted = raw
                gap_used = 0.0
                overflow = 0.0
                recovery_action = "exact_fit"
                review_required = False
                review_reason = ""
            elif raw <= target * preferred_tempo:
                tempo = raw / target
                fitted = target
                gap_used = 0.0
                overflow = 0.0
                recovery_action = "tempo_fit"
                review_required = False
                review_reason = ""
            else:
                dur_at_pref = raw / preferred_tempo
                excess_at_pref = dur_at_pref - target
                if excess_at_pref <= usable_gap:
                    tempo = preferred_tempo
                    fitted = dur_at_pref
                    gap_used = max(0.0, fitted - target)
                    overflow = 0.0
                    recovery_action = "gap_redistribution"
                    review_required = False
                    review_reason = ""
                else:
                    max_target = target + usable_gap
                    req_tempo_with_gap = raw / max_target if max_target > 0 else max_tempo
                    if req_tempo_with_gap <= max_tempo:
                        tempo = min(max_tempo, max(preferred_tempo, req_tempo_with_gap))
                        fitted = raw / tempo
                        gap_used = max(0.0, fitted - target)
                        overflow = 0.0
                        recovery_action = "tempo_and_gap"
                        review_required = False
                        review_reason = ""
                    else:
                        tempo = max_tempo
                        fitted = raw / tempo
                        gap_used = usable_gap
                        overflow = max(0.0, fitted - (target + usable_gap))
                        if overflow <= 0.035 and (available_gap - usable_gap) >= overflow:
                            gap_used += overflow
                            overflow = 0.0
                            recovery_action = "tempo_and_gap"
                            review_required = False
                            review_reason = ""
                        else:
                            recovery_action = "review_required"
                            review_required = True
                            review_reason = "tempo_limit_and_insufficient_gap"

            placement_start = start
            placement_end = placement_start + fitted
            overlap = previous_placement_end is not None and placement_start < previous_placement_end - 1e-9
            if overlap:
                review_required = True
                review_reason = "source_overlap"
                recovery_action = "review_required"

            if total_duration is not None and placement_end > total_duration + 1e-6:
                avail_to_end = max(0.0, total_duration - placement_start)
                if avail_to_end > 0 and (raw / avail_to_end) <= max_tempo:
                    tempo = min(max_tempo, raw / avail_to_end)
                    fitted = raw / tempo
                    placement_end = placement_start + fitted
                    gap_used = max(0.0, fitted - target)
                    overflow = 0.0
                    recovery_action = "end_bounded_tempo"
                else:
                    review_required = True
                    review_reason = "video_end_overflow"
                    recovery_action = "review_required"

        plan.append({
            "segment_id": segment.id,
            "source_start": start,
            "source_end": end,
            "target_duration": target,
            "raw_duration": raw,
            "required_tempo": required_tempo,
            "tempo": tempo,
            "fitted_duration": fitted,
            "overflow": overflow,
            "available_gap": available_gap,
            "gap_used": gap_used,
            "placement_start": placement_start,
            "placement_end": placement_end,
            "recovery_action": recovery_action,
            "review_required": review_required,
            "review_reason": review_reason,
        })
        previous_placement_end = placement_end
    return plan


def recover_timing(
    segments: Sequence[Segment],
    raw_durations: Sequence[float],
    *,
    max_tempo: float = 2.0,
    preferred_tempo: float = 1.25,
    max_gap_borrow: float = 0.85,
    min_pause: float = 0.15,
    total_duration: float | None = None,
) -> list[dict[str, Any]]:
    """Convenience wrapper for plan_timing with allow_gap_recovery=True."""
    return plan_timing(
        segments,
        raw_durations,
        max_tempo=max_tempo,
        allow_gap_recovery=True,
        preferred_tempo=preferred_tempo,
        max_gap_borrow=max_gap_borrow,
        min_pause=min_pause,
        total_duration=total_duration,
    )


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


def is_passthrough_text(source_text: str | None, text_km: str | None) -> bool:
    """Check if text_km is a legitimate unchanged passthrough of source_text.

    Used when NLLB leaves foreign proper names, loanwords, or acronyms
    unchanged because there is no Khmer translation (e.g. 'Selim!', 'Selina!').
    Matches after Unicode NFKC normalization, lowercasing, and stripping
    whitespace and punctuation.
    Rejects: empty text, mismatching words, or text containing Chinese.
    """
    if not source_text or not text_km:
        return False
    # Any Chinese characters in output are always rejected
    if any("\u4e00" <= c <= "\u9fff" for c in text_km):
        return False
    import re, unicodedata
    src_norm = re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", str(source_text))).lower()
    km_norm = re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", str(text_km))).lower()
    if not src_norm or not km_norm:
        return False
    return src_norm == km_norm
