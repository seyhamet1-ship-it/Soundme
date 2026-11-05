from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

log = logging.getLogger("dubber.asr_chunking")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


DEFAULT_CHUNK_DURATION = _env_float("ASR_CHUNK_DURATION_SEC", 300.0)
DEFAULT_CHUNK_OVERLAP = _env_float("ASR_CHUNK_OVERLAP_SEC", 1.5)
DEFAULT_CHUNK_RETRIES = _env_int("ASR_CHUNK_RETRIES", 2)
DEFAULT_MAX_NEW_TOKENS = _env_int("ASR_MAX_NEW_TOKENS", 128)
DEFAULT_BEAM_SIZE = _env_int("ASR_BEAM_SIZE", 1)
@dataclass
class AudioChunk:
    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class ChunkTranscription:
    index: int
    start: float
    end: float
    segments: list[Any] = field(default_factory=list)
    status: str = "pending"
    processing_time: float = 0.0
    attempts: int = 0
    error: str | None = None
    language: str | None = None
    probability: float = 0.0


def compute_source_fingerprint(path: str | Path) -> str:
    path = Path(path)
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
            size += len(block)
    return f"sha256:{h.hexdigest()}:size:{size}"


source_fingerprint = compute_source_fingerprint


def get_audio_duration(path: str | Path) -> float:
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as handle:
            fr = handle.getframerate()
            if fr > 0:
                return handle.getnframes() / fr
    except Exception:
        pass
    try:
        import json
        import subprocess
        from app.pipeline import FFPROBE
        data = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout)
        return float(data.get("format", {}).get("duration", 0.0))
    except Exception:
        return 0.0


audio_duration = get_audio_duration


def read_audio_chunk(path: str | Path, start_sec: float, end_sec: float) -> "np.ndarray | None":
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getsampwidth() != 2:
                return None
            rate = handle.getframerate()
            channels = handle.getnchannels()
            total = handle.getnframes()
            if rate <= 0:
                return None
            start_frame = max(0, min(total, int(round(start_sec * rate))))
            end_frame = max(start_frame, min(total, int(round(end_sec * rate))))
            if end_frame <= start_frame:
                return np.zeros(0, dtype=np.float32)
            handle.setpos(start_frame)
            raw = handle.readframes(end_frame - start_frame)
    except Exception:
        return None
    if not raw:
        return np.zeros(0, dtype=np.float32)
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != 16000 and data.size:
        new_size = max(1, round(data.size * 16000 / rate))
        data = np.interp(
            np.linspace(0, data.size - 1, new_size),
            np.arange(data.size), data,
        ).astype(np.float32)
    return data


extract_chunk = read_audio_chunk
load_chunk = read_audio_chunk


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
def create_chunks(duration, chunk_duration=DEFAULT_CHUNK_DURATION, overlap=DEFAULT_CHUNK_OVERLAP):
    duration = float(duration or 0.0)
    if duration <= 0:
        return []
    chunk_duration = float(chunk_duration or DEFAULT_CHUNK_DURATION)
    overlap = float(overlap or 0.0)
    if chunk_duration <= 0:
        chunk_duration = DEFAULT_CHUNK_DURATION
    if overlap < 0:
        overlap = 0.0
    if overlap >= chunk_duration:
        overlap = max(0.0, chunk_duration - 1.0)
    if duration <= chunk_duration + 1e-9:
        return [AudioChunk(0, 0.0, duration)]
    step = chunk_duration - overlap
    if step <= 0:
        step = chunk_duration
    chunks = []
    start = 0.0
    idx = 0
    guard = 0
    while start < duration - 1e-9 and guard < 100000:
        guard += 1
        end = min(duration, start + chunk_duration)
        if end - start < 0.25:
            if chunks:
                chunks[-1].end = duration
            break
        chunks.append(AudioChunk(idx, float(start), float(end)))
        if end >= duration - 1e-9:
            break
        start += step
        idx += 1
        if start >= duration - 0.5:
            if chunks and chunks[-1].end < duration:
                chunks[-1].end = duration
            break
    if not chunks:
        chunks = [AudioChunk(0, 0.0, duration)]
    if chunks[-1].end < duration:
        chunks[-1].end = duration
    return chunks


make_chunks = create_chunks
split_into_chunks = create_chunks
chunk_ranges = create_chunks
split_audio = create_chunks


def _norm_text(text):
    import re
    import unicodedata
    s = unicodedata.normalize("NFKC", str(text or ""))
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[^\w\s]", "", s)
    return s


def _segments_from_obj(obj):
    if obj is None:
        return []
    if isinstance(obj, dict):
        if "segments" in obj:
            return list(obj.get("segments") or [])
        return [obj]
    segs = getattr(obj, "segments", None)
    if segs is not None:
        return list(segs)
    return [obj]


def _to_segment_ns(seg):
    from types import SimpleNamespace
    if isinstance(seg, SimpleNamespace):
        return seg
    if isinstance(seg, dict):
        return SimpleNamespace(
            start=float(seg.get("start", 0.0)),
            end=float(seg.get("end", 0.0)),
            text=str(seg.get("text", seg.get("source_text", ""))).strip(),
        )
    return SimpleNamespace(
        start=float(getattr(seg, "start", 0.0)),
        end=float(getattr(seg, "end", 0.0)),
        text=str(getattr(seg, "text", getattr(seg, "source_text", ""))).strip(),
    )


def _seg_start(seg):
    return float(seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", 0.0))


def _seg_end(seg):
    return float(seg.get("end") if isinstance(seg, dict) else getattr(seg, "end", 0.0))


def _seg_text(seg):
    if isinstance(seg, dict):
        return str(seg.get("text", seg.get("source_text", ""))).strip()
    return str(getattr(seg, "text", getattr(seg, "source_text", ""))).strip()
def validate_segments(segments, duration, epsilon=1e-6):
    prev_start = -1e-9
    prev_end = -1e-9
    for seg in segments:
        start = _seg_start(seg)
        end = _seg_end(seg)
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("segment timestamp is not finite")
        if start < -epsilon:
            raise ValueError(f"segment start {start} < 0")
        if end <= start:
            raise ValueError(f"segment end {end} <= start {start}")
        if end > float(duration) + 1e-3:
            raise ValueError(f"segment end {end} > audio duration {duration}")
        if start + 1e-9 < prev_start - 1e-9:
            raise ValueError("segments are not in chronological order")
        prev_start = start
        prev_end = max(prev_end, end)
    return True


validate_timestamps = validate_segments


def deduplicate_overlap(segments, duration=None):
    segs = [_to_segment_ns(s) for s in segments]
    segs.sort(key=lambda s: (s.start, s.end))
    out = []
    for s in segs:
        duplicate_of = None
        for existing in out:
            overlap_start = max(existing.start, s.start)
            overlap_end = min(existing.end, s.end)
            if overlap_end <= overlap_start:
                continue
            ne = _norm_text(existing.text)
            ns = _norm_text(s.text)
            if not ne or not ns:
                continue
            if ne == ns or ne in ns or ns in ne:
                shorter = min(len(ne), len(ns))
                longer = max(len(ne), len(ns))
                if longer == 0 or shorter / longer >= 0.6:
                    duplicate_of = existing
                    break
        if duplicate_of is not None:
            if len(_norm_text(s.text)) > len(_norm_text(duplicate_of.text)):
                duplicate_of.text = s.text
            duplicate_of.start = min(duplicate_of.start, s.start)
            duplicate_of.end = max(duplicate_of.end, s.end)
            continue
        out.append(s)
    if duration is not None:
        for s in out:
            s.start = max(0.0, s.start)
            s.end = min(float(duration), s.end)
    out.sort(key=lambda s: (s.start, s.end))
    return out


def merge_chunk_segments(chunk_results, duration=None):
    all_segments = []
    for cr in sorted(chunk_results, key=lambda c: (c.get("index") if isinstance(c, dict) else getattr(c, "index", 0))):
        for seg in _segments_from_obj(cr):
            all_segments.append(_to_segment_ns(seg))
    all_segments.sort(key=lambda s: (s.start, s.end))
    deduped = deduplicate_overlap(all_segments, duration=duration)
    if duration is not None:
        validate_segments(deduped, duration)
    return deduped


merge_chunks = merge_chunk_segments
merge_transcript_chunks = merge_chunk_segments
merge_chunk_transcripts = merge_chunk_segments


def _transcribe_samples(model, samples, source_lang, offset, duration, chunk_end, beam_size, max_new_tokens, retries):
    from types import SimpleNamespace
    result = ChunkTranscription(index=0, start=offset, end=chunk_end)
    language = None if source_lang == "auto" else source_lang
    last_exc = None
    attempts = 0

    def _call(vad):
        if hasattr(model, "_load_wav"):
            tr = model.transcribe(samples, source_lang=source_lang, sample_rate=16000)
            info = SimpleNamespace(language=tr.language, language_probability=tr.language_probability)
            return iter(tr.segments), info
        return model.transcribe(
            samples,
            language=language,
            vad_filter=vad,
            beam_size=beam_size,
            condition_on_previous_text=False,
            max_new_tokens=max_new_tokens,
            hallucination_silence_threshold=2.0,
            temperature=0.0,
        )

    for attempt in range(max(1, retries + 1)):
        attempts = attempt + 1
        try:
            segs_iter, info = _call(True)
            segs = list(segs_iter)
            if not segs:
                segs_iter, info = _call(False)
                segs = list(segs_iter)
            converted = []
            for s in segs:
                text = str(getattr(s, "text", "")).strip()
                if not text:
                    continue
                st = float(getattr(s, "start", 0.0)) + offset
                en = float(getattr(s, "end", 0.0)) + offset
                if st < offset:
                    st = offset
                if en > chunk_end:
                    en = chunk_end
                if en <= st:
                    continue
                if en > duration:
                    en = duration
                if en <= st:
                    continue
                converted.append(SimpleNamespace(start=st, end=en, text=text))
            result.segments = converted
            result.status = "completed"
            result.attempts = attempts
            result.language = getattr(info, "language", None)
            result.probability = float(getattr(info, "language_probability", 0.0) or 0.0)
            if result.probability == 0.0:
                result.probability = 1.0
            return result
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(min(2.0 * (attempt + 1), 8.0))
    result.status = "failed"
    result.attempts = attempts
    result.error = f"chunk {offset:.3f}-{chunk_end:.3f}s: {type(last_exc).__name__}: {last_exc}"
    return result
class ChunkedASR:
    def __init__(self, model, chunk_duration=DEFAULT_CHUNK_DURATION, overlap=DEFAULT_CHUNK_OVERLAP,
                 max_retries=DEFAULT_CHUNK_RETRIES, beam_size=DEFAULT_BEAM_SIZE,
                 max_new_tokens=DEFAULT_MAX_NEW_TOKENS):
        self.model = model
        self.chunk_duration = float(chunk_duration)
        self.overlap = float(overlap)
        self.max_retries = int(max_retries)
        self.beam_size = int(beam_size)
        self.max_new_tokens = int(max_new_tokens)

    def transcribe(self, audio, source_lang="auto", duration=None, root=None, job_id=None,
                   checkpoint_path=None, source_fingerprint_value=None, progress_callback=None, **_kwargs):
        from app.providers import Transcript
        from types import SimpleNamespace

        audio = Path(audio)
        if duration is None:
            duration = get_audio_duration(audio)
        duration = float(duration or 0.0)
        if duration <= 0:
            raise ValueError("audio duration is invalid")

        if checkpoint_path is None:
            base = Path(root) if root is not None else audio.parent
            checkpoint_path = base / "asr_checkpoint.json"
        checkpoint_path = Path(checkpoint_path)

        fp = source_fingerprint_value or compute_source_fingerprint(audio)
        chunks = create_chunks(duration, self.chunk_duration, self.overlap)

        checkpoint = None
        try:
            if checkpoint_path.exists():
                data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if (
                    data.get("source_fingerprint") == fp
                    and abs(float(data.get("audio_duration", duration)) - duration) < 1e-6
                    and abs(float(data.get("chunk_duration", self.chunk_duration)) - self.chunk_duration) < 1e-9
                    and abs(float(data.get("overlap", self.overlap)) - self.overlap) < 1e-9
                ):
                    checkpoint = data
        except Exception as exc:
            log.warning("ASR checkpoint unreadable (%s); reprocessing", exc)
            checkpoint = None

        completed_map = {}
        detected_language = None
        detected_prob = 1.0
        if checkpoint:
            for c in checkpoint.get("chunks", []):
                if c.get("status") == "completed":
                    try:
                        segs = [_to_segment_ns(s) for s in (c.get("segments") or [])]
                        validate_segments(segs, duration)
                        completed_map[int(c.get("index", 0))] = c
                    except Exception:
                        pass
            detected_language = checkpoint.get("language")
            if detected_language:
                detected_language = str(detected_language)
            detected_prob = float(checkpoint.get("probability", 1.0) or 1.0)

        effective_lang = source_lang
        if source_lang == "auto" and detected_language:
            effective_lang = detected_language

        chunk_dicts = [None] * len(chunks)
        for idx, c in completed_map.items():
            if 0 <= idx < len(chunk_dicts):
                chunk_dicts[idx] = c

        for i, chunk in enumerate(chunks):
            if chunk_dicts[i] is not None:
                continue
            t0 = time.time()
            lang_for_chunk = effective_lang
            samples = read_audio_chunk(audio, chunk.start, chunk.end)
            if samples is None:
                samples = self._ffmpeg_chunk(audio, chunk.start, chunk.end)
            if samples is None:
                samples = np.zeros(0, dtype=np.float32)

            res = _transcribe_samples(
                self.model, samples, source_lang=lang_for_chunk,
                offset=chunk.start, duration=duration, chunk_end=chunk.end,
                beam_size=self.beam_size, max_new_tokens=self.max_new_tokens,
                retries=self.max_retries,
            )
            res.index = i
            res.start = chunk.start
            res.end = chunk.end
            proc_time = round(time.time() - t0, 3)
            res.processing_time = proc_time

            if res.status != "completed":
                chunk_dicts[i] = {
                    "index": i, "start": chunk.start, "end": chunk.end,
                    "status": "failed", "processing_time": proc_time,
                    "attempts": res.attempts, "error": res.error, "segments": [],
                }
                self._save_checkpoint(checkpoint_path, fp, duration, chunk_dicts, chunks, detected_language, detected_prob)
                raise RuntimeError(res.error or f"ASR chunk {i} failed")

            if source_lang == "auto" and detected_language is None and res.language:
                detected_language = res.language
                detected_prob = res.probability or detected_prob
                effective_lang = detected_language

            chunk_dicts[i] = {
                "index": i, "start": chunk.start, "end": chunk.end,
                "status": "completed", "processing_time": proc_time,
                "attempts": res.attempts, "error": None,
                "segments": [
                    {"start": float(s.start), "end": float(s.end), "text": str(s.text).strip()}
                    for s in res.segments
                ],
            }
            self._save_checkpoint(checkpoint_path, fp, duration, chunk_dicts, chunks, detected_language, detected_prob)

            completed_count = sum(1 for c in chunk_dicts if c is not None and c.get("status") == "completed")
            progress = 18 + int((completed_count / max(1, len(chunks))) * 18)
            progress = min(36, progress)
            if job_id is not None:
                try:
                    from app.storage import update_job
                    update_job(job_id, stage="transcribe", progress=progress)
                except Exception:
                    pass
            if progress_callback is not None:
                try:
                    progress_callback(completed_count, len(chunks), res)
                except Exception:
                    pass

        merged = merge_chunk_segments(chunk_dicts, duration)
        validate_segments(merged, duration)

        language = detected_language or "en"
        if not detected_language and chunk_dicts and chunk_dicts[0]:
            language = chunk_dicts[0].get("language", language) or language
        prob = detected_prob or 1.0

        final_payload = self._checkpoint_payload(fp, duration, chunk_dicts, chunks, detected_language, detected_prob)
        final_payload["segments"] = [
            {"start": float(s.start), "end": float(s.end), "text": str(getattr(s, "text", "")).strip()}
            for s in merged
        ]
        final_payload["language"] = language
        final_payload["probability"] = prob
        _atomic_write_json(checkpoint_path, final_payload)

        seg_ns = [SimpleNamespace(start=float(s.start), end=float(s.end), text=str(getattr(s, "text", "")).strip()) for s in merged]
        return Transcript(seg_ns, language, float(prob))

    def _save_checkpoint(self, checkpoint_path, fp, duration, chunk_dicts, chunks, language, prob):
        payload = self._checkpoint_payload(fp, duration, chunk_dicts, chunks, language, prob)
        _atomic_write_json(checkpoint_path, payload)

    def _checkpoint_payload(self, fp, duration, chunk_dicts, chunks, language, prob):
        chunk_list = []
        for i, chunk in enumerate(chunks):
            cd = chunk_dicts[i]
            if cd is None:
                cd = {
                    "index": i, "start": chunk.start, "end": chunk.end,
                    "status": "pending", "processing_time": 0.0,
                    "attempts": 0, "error": None, "segments": [],
                }
            chunk_list.append(cd)
        return {
            "version": 2,
            "source_fingerprint": fp,
            "audio_duration": float(duration),
            "chunk_duration": float(self.chunk_duration),
            "overlap": float(self.overlap),
            "language": language,
            "probability": float(prob or 1.0),
            "chunks": chunk_list,
            "updated_at": time.time(),
        }

    def _ffmpeg_chunk(self, audio, start, end):
        try:
            import subprocess
            import tempfile
            from app.pipeline import FFMPEG
            tmp = Path(tempfile.gettempdir()) / f"asr_chunk_{int(start)}_{int(end)}.wav"
            subprocess.run(
                [FFMPEG, "-y", "-ss", str(start), "-t", str(end - start), "-i", str(audio),
                 "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)],
                capture_output=True, check=True,
            )
            return read_audio_chunk(tmp, 0.0, end - start)
        except Exception as exc:
            log.warning("ffmpeg chunk fallback failed: %s", exc)
            return None


ChunkedTranscriber = ChunkedASR
LongFormASR = ChunkedASR


def transcribe_chunked(*args, **kwargs):
    if args and hasattr(args[0], "transcribe") and not isinstance(args[0], (str, Path)):
        model = args[0]
        audio = args[1] if len(args) > 1 else kwargs.pop("audio")
        rest = list(args[2:])
        init_keys = {"chunk_duration", "overlap", "max_retries", "beam_size", "max_new_tokens"}
        init_kwargs = {k: v for k, v in kwargs.items() if k in init_keys}
        asr = ChunkedASR(model, **init_kwargs)
        call_kwargs = {k: v for k, v in kwargs.items() if k not in init_keys}
        if "source_lang" not in call_kwargs and rest:
            call_kwargs["source_lang"] = rest.pop(0)
        return asr.transcribe(audio, **call_kwargs)
    raise TypeError("transcribe_chunked requires a model as first argument")
