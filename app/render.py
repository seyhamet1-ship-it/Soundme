from __future__ import annotations
import json,os,subprocess,time,wave
from pathlib import Path
from typing import Any
import numpy as np
from app.providers import synthesize_khmer
from app.audio_quality import require_real_speech
from app.segments import Segment, is_khmer_text, is_passthrough_text, plan_timing, recover_timing, write_srt, write_vtt
from app.storage import get_job, update_job
from app.pipeline import run, probe
import shutil
FFMPEG=os.getenv("FFMPEG","ffmpeg"); FFPROBE=os.getenv("FFPROBE","ffprobe")
from app.config import TIMING_PREFERRED_TEMPO_MAX, TIMING_MAX_TEMPO, TIMING_MAX_GAP_BORROW, TIMING_MIN_BREATH_PAUSE

def wav_duration(path:Path)->float:
 """Read a WAV duration with validation suitable for timing decisions."""
 try:
  with wave.open(str(path),"rb") as f:
   if f.getframerate()<=0 or f.getnframes()<=0: raise ValueError("invalid WAV frame count or sample rate")
   return f.getnframes()/f.getframerate()
 except (wave.Error,OSError,EOFError) as exc:
  raise ValueError(f"invalid synthesized WAV: {path.name}") from exc

def fit_wav_atempo(source:Path,destination:Path,tempo:float,*,job_id:str|None=None)->float:
 """Fit with FFmpeg atempo (pitch-preserving) and measure the real output."""
 if not 1.0<=tempo<=TIMING_MAX_TEMPO: raise ValueError("tempo is outside the configured timing policy")
 run([FFMPEG,"-y","-i",str(source),"-filter:a",f"atempo={tempo:.8f}","-ac","1","-ar","48000","-c:a","pcm_s16le",str(destination)],job_id=job_id)
 return wav_duration(destination)

def _timing_review(job_id:str,segments:list[Segment],providers:list[str],fit_stats:list[dict[str,Any]])->None:
 """Reuse the existing editable review lifecycle; do not publish partial media."""
 update_job(job_id,status="awaiting_review",stage="timing_review",progress=54,segments=[s.__dict__ for s in segments],providers=providers,
  error={"code":"TIMING_OVERFLOW_REVIEW_REQUIRED","message_en":"One or more synthesized speech segments exceed their source timing slot at the allowed tempo.","message_km":"ផ្នែកសំឡេងមួយ ឬច្រើនវែងជាងពេលវេលាដើមដែលអនុញ្ញាត។ សូមពិនិត្យអត្ថបទបកប្រែ។"},
  timing_review={"code":"TIMING_OVERFLOW_REVIEW_REQUIRED","fit_stats":fit_stats},timings={"fit_stats":fit_stats})
def render(job_id:str,source:Path,root:Path,segments:list[Segment],duration:float,opts:dict[str,Any],transcript:Any,providers:list[str],video_stream:dict[str,Any],started:float,counters:dict[str,Any]|None=None,stage_times:dict[str,float]|None=None)->None:
 if (get_job(job_id) or {}).get("status") == "canceled":
  if root.exists(): shutil.rmtree(root, ignore_errors=True)
  return
 ctr = counters if counters is not None else {}
 stg = stage_times if stage_times is not None else {}
 ctr.setdefault("tts_total", len(segments))
 ctr.setdefault("tts_skipped", 0)
 ctr.setdefault("tts_cached", 0)
 ctr.setdefault("tts_computed", 0)
 from app.long_video import compute_content_hash, get_cached_segment_audio, store_cached_segment_audio

 t_tts = time.time()
 speech=[]; tts=[]; fit_stats=[]; prev_end=0.0; demo_audio=False; update_job(job_id,stage="voice",progress=54)

 from concurrent.futures import ThreadPoolExecutor

 voice = opts.get("voice", "km-KH-SreymomNeural")
 tts_providers: dict[int, str] = {}
 to_compute: list[tuple[int, Segment, Path, float, str, str]] = []

 for idx, s in enumerate(segments):
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  text_km = getattr(s, 'text_km', None) or (s.get('text_km') if isinstance(s, dict) else '')
  if not text_km or not str(text_km).strip():
   raise ValueError('Khmer translation is empty.')
  src_text = getattr(s, 'source_text', None) or (s.get('source_text') if isinstance(s, dict) else None)
  if providers[idx].startswith('nllb') and not is_khmer_text(text_km) and not is_passthrough_text(src_text, text_km):
   raise ValueError('Translation provider returned non-Khmer text.')
  slot = max(.2, s.end - s.start)
  out = root / f"voice-{idx:04}.wav"
  content_h = compute_content_hash(text_km, voice, slot)
  cached_disk = get_cached_segment_audio(content_h)

  # Do not reuse the job-local voice-NNNN.wav here. A review/resume may have
  # changed text_km while retaining the same segment id, so that file is not a
  # reliable content checkpoint. The content-addressed cache includes text,
  # voice, and slot and is safe to reuse.
  if cached_disk is not None and cached_disk.stat().st_size > 1000:
   shutil.copyfile(cached_disk, out)
   tts_providers[idx] = "content-cache"
   ctr["tts_cached"] += 1
  else:
   est_dur = len(s.text_km.strip()) / 12.0
   rate_pct = min(30, max(0, int(round((est_dur / slot - 1.0) * 100)))) if est_dur > slot else 0
   to_compute.append((idx, s, out, slot, f"+{rate_pct}%", content_h))

 if to_compute:
  def _synth_worker(item: tuple[int, Segment, Path, float, str, str]) -> tuple[int, str]:
   i, seg, out_p, sl, r_rate, c_hash = item
   if (get_job(job_id) or {}).get("status") == "canceled":
    return i, "canceled"
   p_used = synthesize_khmer(seg.text_km, voice, out_p, sl, i + 1, rate=r_rate)
   if p_used.startswith("demo"):
    if p_used != "demo-tone-not-speech" or os.getenv("DEMO_MODE", "").strip().lower() not in {"1", "true", "yes"}:
     raise ValueError(f"Non-speech/demo TTS provider cannot complete a dubbing job: {p_used}")
   elif p_used not in {"edge-tts", "azure-speech"} and not p_used.startswith("mms-"):
    raise ValueError(f"Unrecognized TTS provider cannot complete a dubbing job: {p_used}")
   else:
    require_real_speech(out_p)
   try: store_cached_segment_audio(c_hash, out_p)
   except Exception: pass
   return i, p_used

  concurrency = int(os.getenv("TTS_CONCURRENCY", "2"))
  completed_tts = 0
  total_tts = len(to_compute)
  with ThreadPoolExecutor(max_workers=concurrency) as pool:
   for i, prov in pool.map(_synth_worker, to_compute):
    if prov == "canceled":
     if root.exists(): shutil.rmtree(root, ignore_errors=True)
     return
    tts_providers[i] = prov
    ctr["tts_computed"] += 1
    completed_tts += 1
    cur_p = 54 + int((completed_tts / max(1, total_tts)) * 20)
    update_job(job_id, stage="voice", progress=min(74, cur_p))

 stg["tts"] = round(time.time() - t_tts, 3)
 t_fitmix = time.time()
 update_job(job_id, stage="fit", progress=75)
 raw_durations = [wav_duration(root/f"voice-{idx:04}.wav") for idx in range(len(segments))]

 # Automatic timing recovery: fit with tempo and safe gap redistribution
 fit_stats = recover_timing(
  segments,
  raw_durations,
  max_tempo=TIMING_MAX_TEMPO,
  preferred_tempo=TIMING_PREFERRED_TEMPO_MAX,
  max_gap_borrow=TIMING_MAX_GAP_BORROW,
  min_pause=TIMING_MIN_BREATH_PAUSE,
  total_duration=duration,
 )

 # If any segment still exceeds available bounds, try retry with higher rate (+35%)
 for idx, stat in enumerate(fit_stats):
  if stat.get("review_required"):
   seg = segments[idx]
   out_p = root / f"voice-{idx:04}.wav"
   slot = max(.2, seg.end - seg.start)
   try:
    p_retry = synthesize_khmer(seg.text_km, voice, out_p, slot, idx + 1, rate="+35%")
    require_real_speech(out_p)
    tts_providers[idx] = p_retry
    raw_durations[idx] = wav_duration(out_p)
    store_cached_segment_audio(compute_content_hash(seg.text_km, voice, slot), out_p)
   except Exception:
    pass

 fit_stats = recover_timing(
  segments,
  raw_durations,
  max_tempo=TIMING_MAX_TEMPO,
  preferred_tempo=TIMING_PREFERRED_TEMPO_MAX,
  max_gap_borrow=TIMING_MAX_GAP_BORROW,
  min_pause=TIMING_MIN_BREATH_PAUSE,
  total_duration=duration,
 )

 if any(stat["review_required"] for stat in fit_stats):
  _timing_review(job_id, segments, providers, fit_stats)
  return

 fitted_paths = []
 for idx, stat in enumerate(fit_stats):
  fitted = root / f"fit-{idx:04}.wav"
  tempo = float(stat["tempo"])
  actual = fit_wav_atempo(root / f"voice-{idx:04}.wav", fitted, tempo, job_id=job_id)
  target = float(stat["target_duration"])
  allowed = target + float(stat.get("gap_used", 0.0))

  # atempo operates in frames; adjust with remaining gap or minor tempo nudge if needed
  if actual > allowed + 1e-4 and tempo < TIMING_MAX_TEMPO:
   avail_gap = float(stat.get("available_gap", 0.0))
   max_gap = avail_gap * TIMING_MAX_GAP_BORROW
   excess = actual - target
   if excess <= max_gap:
    stat["gap_used"] = max(0.0, excess)
    allowed = target + stat["gap_used"]
   else:
    tempo = min(TIMING_MAX_TEMPO, tempo * (actual / max(0.1, allowed)))
    actual = fit_wav_atempo(root / f"voice-{idx:04}.wav", fitted, tempo, job_id=job_id)
    excess = actual - target
    if excess <= max_gap:
     stat["gap_used"] = max(0.0, excess)
     allowed = target + stat["gap_used"]

  stat["tempo"] = tempo
  stat["fitted_duration"] = actual
  stat["placement_end"] = float(stat["placement_start"]) + actual
  stat["overflow"] = max(0.0, actual - allowed)
  stat["review_required"] = bool(stat["overflow"] > 1e-4)
  stat["review_reason"] = "measured_overflow" if stat["review_required"] else ""
  fitted_paths.append(fitted)
  cur_p = 75 + int(((idx + 1) / max(1, len(fit_stats))) * 6)
  update_job(job_id, stage="fit", progress=min(81, cur_p))

 if any(stat["review_required"] for stat in fit_stats):
  _timing_review(job_id, segments, providers, fit_stats)
  return

 frames = int(round(duration * 48000))
 if frames <= 0: raise ValueError("video duration is invalid")
 for stat in fit_stats:
  if float(stat["placement_end"]) * 48000 > frames + 48:
   if (float(stat["placement_end"]) - duration) <= 0.1:
    stat["placement_end"] = min(duration, float(stat["placement_end"]))
   else:
    stat["review_required"] = True
    stat["review_reason"] = "video_end_overflow"
 if any(stat["review_required"] for stat in fit_stats):
  _timing_review(job_id, segments, providers, fit_stats)
  return

 dub = np.zeros(frames, dtype=np.float32)
 for idx, (stat, fitted) in enumerate(zip(fit_stats, fitted_paths, strict=True)):
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  tts_provider = tts_providers.get(idx, "unknown")
  if tts_provider.startswith("demo"): demo_audio = True
  tts.append(tts_provider)
  with wave.open(str(fitted), "rb") as f:
   if f.getframerate() != 48000 or f.getnchannels() != 1 or f.getsampwidth() != 2:
    raise ValueError(f"fitted WAV has unexpected format: {fitted.name}")
   res = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float32) / 32768
  start_idx = int(round(float(stat["placement_start"]) * 48000))
  end_idx = start_idx + len(res)
  if start_idx < 0: raise RuntimeError("timing bounds changed after validation")
  if start_idx >= len(dub): continue
  if end_idx > len(dub):
   res = res[:len(dub) - start_idx]
   end_idx = len(dub)
  dub[start_idx:end_idx] += res
  speech.append(float(stat["fitted_duration"]))

 peak = float(np.max(np.abs(dub))) if len(dub) else 0
 if peak: dub *= min(.89, .89 / peak)
 audio = root / "dub.wav"
 with wave.open(str(audio), "wb") as wf:
  wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(48000)
  wf.writeframes((np.clip(dub, -.89, .89) * 32767).astype("<i2").tobytes())

 stg["fitmix"] = round(time.time() - t_fitmix, 3)
 (root / "transcript.json").write_text(json.dumps({
  "detected_language": transcript.language,
  "language_probability": transcript.language_probability,
  "mode": "demo-plumbing" if demo_audio else "real",
  "segments": [s.__dict__ for s in segments],
  "translation_providers": providers,
  "tts_providers": tts,
  "fit_stats": fit_stats,
  "counters": ctr,
  "stage_times": stg
 }, ensure_ascii=False, indent=2), encoding="utf-8")
 (root / "subtitles.srt").write_text(write_srt(segments), encoding="utf-8")
 (root / "subtitles.vtt").write_text(write_vtt(segments), encoding="utf-8")

 t_mux = time.time()
 update_job(job_id, stage="mix", progress=82)
 video = root / "dubbed.mp4"
 args = [FFMPEG, "-y", "-i", str(source), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
 if opts.get("keep_background"):
  args = [FFMPEG, "-y", "-i", str(source), "-i", str(audio), "-filter_complex", "[0:a:0]volume=0.12[bg];[bg][1:a:0]amix=inputs=2:duration=longest:normalize=0[mix]", "-map", "0:v:0", "-map", "[mix]"]
 copy_codecs = {"h264", "hevc", "av1", "vp9"}
 codec_can_copy = video_stream.get("codec_name") in copy_codecs and not opts.get("burn_subtitles")
 args += ["-c:v", "copy" if codec_can_copy else "libx264", "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k", "-t", str(duration), "-movflags", "+faststart"]
 if opts.get("burn_subtitles"):
  path = str(root / "subtitles.srt").replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
  args += ["-vf", f"subtitles='{path}':force_style='FontName=Khmer OS,FontSize=24,Outline=2,Shadow=1,MarginV=30'"]
 run(args + [str(video)])
 stg["mux"] = round(time.time() - t_mux, 3)
 if not video.exists() or video.stat().st_size == 0:
  raise RuntimeError(f"Final dubbed video was not generated or is empty: {video.name}")
 v_meta = probe(video)
 if not any(s["codec_type"] == "video" for s in v_meta.get("streams", [])):
  raise RuntimeError("Final dubbed video is missing video stream")
 if not any(s["codec_type"] == "audio" for s in v_meta.get("streams", [])):
  raise RuntimeError("Final dubbed video is missing audio stream")
 update_job(job_id, status="completed", stage="done", progress=100, output_url=str(video), outputs={"video": video.name, "audio": audio.name, "srt": "subtitles.srt", "vtt": "subtitles.vtt", "json": "transcript.json"}, detected_language=transcript.language, providers={"translation": sorted(set(providers)), "tts": sorted(set(tts))}, segments=[s.__dict__ for s in segments], timings={"segment_durations": speech, "processing_seconds": round(time.time() - started, 2), "fit_stats": fit_stats, "counters": ctr, "stage_times": stg})