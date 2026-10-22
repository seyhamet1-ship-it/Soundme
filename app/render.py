from __future__ import annotations
import json,os,subprocess,time,wave
from pathlib import Path
from typing import Any
import numpy as np
from app.providers import synthesize_khmer
from app.audio_quality import require_real_speech
from app.segments import Segment,is_khmer_text,write_srt,write_vtt
from app.storage import get_job, update_job
from app.pipeline import run, probe
import shutil
FFMPEG=os.getenv("FFMPEG","ffmpeg"); FFPROBE=os.getenv("FFPROBE","ffprobe")
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
 dub=np.zeros(int(duration*48000),dtype=np.float32); speech=[]; tts=[]; fit_stats=[]; prev_end=0.0; demo_audio=False; update_job(job_id,stage="voice",progress=54)

 from concurrent.futures import ThreadPoolExecutor

 voice = opts.get("voice", "km-KH-SreymomNeural")
 tts_providers: dict[int, str] = {}
 to_compute: list[tuple[int, Segment, Path, float, str, str]] = []

 for idx, s in enumerate(segments):
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  if not s.text_km.strip(): raise ValueError("Khmer translation is empty.")
  if providers[idx].startswith("nllb") and not is_khmer_text(s.text_km): raise ValueError("Translation provider returned non-Khmer text.")
  slot = max(.2, s.end - s.start)
  out = root / f"voice-{idx:04}.wav"
  content_h = compute_content_hash(s.text_km, voice, slot)
  cached_disk = get_cached_segment_audio(content_h)

  if out.exists() and out.stat().st_size > 1000:
   tts_providers[idx] = "checkpoint"
   ctr["tts_skipped"] += 1
  elif cached_disk is not None and cached_disk.stat().st_size > 1000:
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
  with ThreadPoolExecutor(max_workers=concurrency) as pool:
   for i, prov in pool.map(_synth_worker, to_compute):
    if prov == "canceled":
     if root.exists(): shutil.rmtree(root, ignore_errors=True)
     return
    tts_providers[i] = prov
    ctr["tts_computed"] += 1

 stg["tts"] = round(time.time() - t_tts, 3)
 t_fitmix = time.time()
 for idx,s in enumerate(segments):
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  slot=max(.2,s.end-s.start); out=root/f"voice-{idx:04}.wav"
  tts_provider = tts_providers.get(idx, "unknown")
  if tts_provider.startswith("demo"): demo_audio = True
  tts.append(tts_provider)
  with wave.open(str(out),"rb") as f: rate=f.getframerate(); data=np.frombuffer(f.readframes(f.getnframes()),dtype="<i2").astype(np.float32)/32768
  raw_dur=len(data)/rate
  tempo=min(1.25,max(1.0,raw_dur/slot)) if raw_dur>slot else 1.0
  if tempo>1.0: data=np.interp(np.arange(int(len(data)/tempo))*tempo,np.arange(len(data)),data).astype(np.float32)
  res=np.interp(np.arange(int(len(data)*48000/rate))*rate/48000,np.arange(len(data)),data).astype(np.float32)
  fitted_dur=len(res)/48000
  start_sec=max(s.start,prev_end-0.08) if prev_end>0 else s.start
  overlap_ms=max(0.0,(prev_end-start_sec)*1000)
  start_idx=int(start_sec*48000); end_idx=min(len(dub),start_idx+len(res))
  if start_idx<len(dub): dub[start_idx:end_idx]+=res[:end_idx-start_idx]
  prev_end=start_sec+fitted_dur; speech.append(fitted_dur)
  stat={"segment_id":s.id,"slot":round(slot,3),"raw_dur":round(raw_dur,3),"atempo":round(tempo,2),"final_dur":round(fitted_dur,3),"overlap_ms":round(overlap_ms,1)}
  fit_stats.append(stat)
 peak=float(np.max(np.abs(dub))) if len(dub) else 0
 if peak: dub*=min(.89,.89/peak)
 audio=root/"dub.wav"
 with wave.open(str(audio),"wb") as wf: wf.setnchannels(1);wf.setsampwidth(2);wf.setframerate(48000);wf.writeframes((np.clip(dub,-.89,.89)*32767).astype("<i2").tobytes())
 stg["fitmix"] = round(time.time() - t_fitmix, 3)
 (root/"transcript.json").write_text(json.dumps({"detected_language":transcript.language,"language_probability":transcript.language_probability,"mode":"demo-plumbing" if demo_audio else "real","segments":[s.__dict__ for s in segments],"translation_providers":providers,"tts_providers":tts,"fit_stats":fit_stats,"counters":ctr,"stage_times":stg},ensure_ascii=False,indent=2),encoding="utf-8")
 (root/"subtitles.srt").write_text(write_srt(segments),encoding="utf-8"); (root/"subtitles.vtt").write_text(write_vtt(segments),encoding="utf-8")
 t_mux = time.time()
 update_job(job_id,stage="mix",progress=82); video=root/"dubbed.mp4"; args=[FFMPEG,"-y","-i",str(source),"-i",str(audio),"-map","0:v:0","-map","1:a:0"]
 if opts.get("keep_background"):
  args=[FFMPEG,"-y","-i",str(source),"-i",str(audio),"-filter_complex","[0:a:0]volume=0.12[bg];[bg][1:a:0]amix=inputs=2:duration=longest:normalize=0[mix]","-map","0:v:0","-map","[mix]"]
 args += ["-c:v","copy" if video_stream.get("codec_name") in {"h264","av1"} and not opts.get("burn_subtitles") else "libx264","-preset","veryfast","-c:a","aac","-b:a","192k","-t",str(duration),"-movflags","+faststart"]
 if opts.get("burn_subtitles"):
  path=str(root/"subtitles.srt").replace("\\","/").replace(":","\\:").replace("'","\\'"); args += ["-vf",f"subtitles='{path}':force_style='FontName=Khmer OS,FontSize=24,Outline=2,Shadow=1,MarginV=30'"]
 run(args+[str(video)])
 stg["mux"] = round(time.time() - t_mux, 3)
 if not video.exists() or video.stat().st_size == 0:
  raise RuntimeError(f"Final dubbed video was not generated or is empty: {video.name}")
 v_meta = probe(video)
 if not any(s["codec_type"] == "video" for s in v_meta.get("streams", [])):
  raise RuntimeError("Final dubbed video is missing video stream")
 if not any(s["codec_type"] == "audio" for s in v_meta.get("streams", [])):
  raise RuntimeError("Final dubbed video is missing audio stream")
 update_job(job_id,status="completed",stage="done",progress=100,output_url=str(video),outputs={"video":video.name,"audio":audio.name,"srt":"subtitles.srt","vtt":"subtitles.vtt","json":"transcript.json"},detected_language=transcript.language,providers={"translation":sorted(set(providers)),"tts":sorted(set(tts))},segments=[s.__dict__ for s in segments],timings={"segment_durations":speech,"processing_seconds":round(time.time()-started,2),"fit_stats":fit_stats,"counters":ctr,"stage_times":stg})
