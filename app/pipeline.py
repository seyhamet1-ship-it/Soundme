from __future__ import annotations
import json,logging,os,subprocess,time
from pathlib import Path
from typing import Any
from app.providers import WhisperASR,translate_khmer
from app.segments import Segment,normalize_numbers,write_srt,write_vtt
from app.storage import get_job, update_job
import shutil
log=logging.getLogger("dubber.pipeline")
FFMPEG=os.getenv("FFMPEG","ffmpeg"); FFPROBE=os.getenv("FFPROBE","ffprobe")
_ACTIVE_PROCS: dict[str, subprocess.Popen[str]] = {}
def kill_job_process(job_id:str)->None:
 proc=_ACTIVE_PROCS.pop(job_id,None)
 if proc and proc.poll() is None:
  try: proc.kill()
  except Exception: pass
def run(args:list[str],job_id:str|None=None)->subprocess.CompletedProcess[str]:
 proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8",errors="replace")
 if job_id: _ACTIVE_PROCS[job_id]=proc
 try:
  out,err=proc.communicate()
  if proc.returncode!=0:
   if job_id and (get_job(job_id) or {}).get("status")=="canceled":
    return subprocess.CompletedProcess(args,proc.returncode,out,err)
   raise subprocess.CalledProcessError(proc.returncode,args,output=out,stderr=err)
  return subprocess.CompletedProcess(args,proc.returncode,out,err)
 finally:
  if job_id: _ACTIVE_PROCS.pop(job_id,None)
def probe(path:Path)->dict[str,Any]:
 data=json.loads(run([FFPROBE,"-v","error","-show_streams","-show_format","-of","json",str(path)]).stdout)
 if not any(x["codec_type"]=="video" for x in data.get("streams",[])): raise ValueError("The uploaded file has no video stream.")
 duration=float(data.get("format",{}).get("duration",0))
 from app.config import MAX_VIDEO_SECONDS, MAX_UPLOAD_BYTES
 if duration<=0: raise ValueError("Video duration is invalid (0 or negative).")
 if duration>MAX_VIDEO_SECONDS:
  from app.i18n import get_km
  actual_min=round(duration/60,1)
  max_min=MAX_VIDEO_SECONDS//60
  raise ValueError(f"Video duration ({actual_min} min) exceeds the {max_min}-minute limit. " + get_km("duration_too_long"))
 file_size=path.stat().st_size
 if file_size>MAX_UPLOAD_BYTES:
  from app.i18n import get_km
  actual_mb=round(file_size/1024/1024,1)
  max_mb=MAX_UPLOAD_BYTES//1024//1024
  raise ValueError(f"File size ({actual_mb} MB) exceeds the {max_mb} MB limit. " + get_km("upload_too_large"))
 # Disk space pre-check: need ~3x input size for intermediate files
 import shutil as _shutil
 needed=file_size*3
 free=_shutil.disk_usage(path.parent).free
 if free<needed:
  from app.i18n import get_km
  raise ValueError(get_km("disk_space_low"))
 return data
def failure_error(exc:BaseException)->dict[str,Any]:
 from app.i18n import get_km
 message=str(exc)[:500]; lower=message.lower()
 if message.startswith("TTS_UNAVAILABLE"): return {"code":"TTS_UNAVAILABLE","message_en":message,"message_km":get_km("tts_unavailable")}
 if "TTS_EMPTY_TEXT" in message: return {"code":"TTS_EMPTY_TEXT","message_en":message,"message_km":get_km("tts_empty_text")}
 if "duration" in lower or "30-minute" in lower: return {"code":"DURATION_TOO_LONG","message_en":message,"message_km":get_km("duration_too_long")}
 if "upload limit" in lower or "500 mb" in lower or "upload_too_large" in lower: return {"code":"UPLOAD_TOO_LARGE","message_en":message,"message_km":get_km("upload_too_large")}
 if "no audio track" in lower: return {"code":"NO_AUDIO_TRACK","message_en":message,"message_km":get_km("no_audio_track")}
 if "no speech was detected" in lower or "no speech detected" in lower: return {"code":"NO_SPEECH_DETECTED","message_en":message,"message_km":get_km("no_speech_detected")}
 if "no video stream" in lower: return {"code":"NO_VIDEO_STREAM","message_en":message,"message_km":get_km("no_video_stream")}
 if "invalid video" in lower: return {"code":"INVALID_VIDEO","message_en":message,"message_km":get_km("invalid_video")}
 if "file is empty" in lower or "empty" in lower: return {"code":"FILE_EMPTY","message_en":message,"message_km":get_km("file_empty")}
 return {"code":"PROCESSING_FAILED","message_en":message,"message_km":get_km("processing_failed")}
def process_video(job_id:str,source_path:str,options:dict[str,Any]|None=None)->None:
 opts=options or {}; root=Path(source_path).parent; source=Path(source_path); started=time.time()
 counters: dict[str, Any] = {
  "extract_skipped": False,
  "asr_total": 0, "asr_skipped": 0, "asr_computed": 0,
  "translate_total": 0, "translate_skipped": 0, "translate_computed": 0,
  "tts_total": 0, "tts_skipped": 0, "tts_cached": 0, "tts_computed": 0,
 }
 stage_times: dict[str, float] = {}
 try:
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  t_probe = time.time()
  update_job(job_id,status="running",stage="validate",progress=3); meta=probe(source); video=next(s for s in meta["streams"] if s["codec_type"]=="video")
  stage_times["probe"] = round(time.time() - t_probe, 3)
  if not any(s["codec_type"]=="audio" for s in meta["streams"]): raise ValueError("This video has no audio track to transcribe.")
  duration=float(meta["format"]["duration"]); wav=root/"source.wav"
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  t_extract = time.time()
  update_job(job_id,stage="extract",progress=8)
  if not (wav.exists() and wav.stat().st_size > 1000):
   run([FFMPEG,"-y","-i",str(source),"-map","0:a:0","-vn","-ac","1","-ar","16000","-c:a","pcm_s16le",str(wav)], job_id=job_id)
  else:
   counters["extract_skipped"] = True
   log.info("Job %s: Audio extraction checkpoint hit; skipped extract", job_id)
  stage_times["extract"] = round(time.time() - t_extract, 3)
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  update_job(job_id,stage="transcribe",progress=18)
  t_asr = time.time()
  asr_ckpt = root / "asr_checkpoint.json"
  if asr_ckpt.exists():
   try:
    asr_data = json.loads(asr_ckpt.read_text(encoding="utf-8"))
    import types
    raw_segs = [types.SimpleNamespace(**s) for s in asr_data["segments"]]
    from app.providers import Transcript
    transcript = Transcript(raw_segs, asr_data["language"], float(asr_data.get("probability", 1.0)))
    counters["asr_total"] = len(raw_segs)
    counters["asr_skipped"] = len(raw_segs)
    counters["asr_computed"] = 0
    log.info("Job %s: ASR checkpoint hit; skipped %d segments", job_id, len(raw_segs))
   except Exception:
    transcript = WhisperASR(os.getenv("ASR_MODEL","small")).transcribe(wav,opts.get("source_lang","auto"))
    counters["asr_total"] = len(transcript.segments)
    counters["asr_skipped"] = 0
    counters["asr_computed"] = len(transcript.segments)
  else:
   transcript = WhisperASR(os.getenv("ASR_MODEL","small")).transcribe(wav,opts.get("source_lang","auto"))
   counters["asr_total"] = len(transcript.segments)
   counters["asr_skipped"] = 0
   counters["asr_computed"] = len(transcript.segments)
   asr_ckpt.write_text(json.dumps({
    "language": transcript.language,
    "probability": transcript.language_probability,
    "segments": [{"start": float(s.start), "end": float(s.end), "text": str(s.text).strip()} for s in transcript.segments]
   }, ensure_ascii=False, indent=2), encoding="utf-8")
  stage_times["asr"] = round(time.time() - t_asr, 3)
  segments=[Segment(i+1,float(s.start),float(s.end),str(s.text).strip()) for i,s in enumerate(transcript.segments) if str(s.text).strip()]
  if not segments: raise ValueError("No speech was detected. Try a video with audible speech.")
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  t_translate = time.time()
  update_job(job_id,detected_language=transcript.language,stage="translate",progress=38)
  trans_ckpt = root / "translation_checkpoint.json"
  saved_trans = {}
  if trans_ckpt.exists():
   try: saved_trans = json.loads(trans_ckpt.read_text(encoding="utf-8"))
   except Exception: saved_trans = {}
  providers = []
  missing_indices = []
  missing_texts = []
  for idx, s in enumerate(segments):
   key = str(s.id)
   if key in saved_trans and saved_trans[key].get("text_km"):
    s.text_km = normalize_numbers(saved_trans[key]["text_km"])
    providers.append(saved_trans[key].get("provider", "nllb-checkpoint"))
    counters["translate_skipped"] += 1
   else:
    providers.append("")
    missing_indices.append(idx)
    missing_texts.append(s.source_text)
  counters["translate_total"] = len(segments)
  if missing_texts:
   from app.providers import translate_khmer_batch
   trans_batch, p = translate_khmer_batch(missing_texts, transcript.language)
   counters["translate_computed"] = len(trans_batch)
   for idx, km in zip(missing_indices, trans_batch):
    segments[idx].text_km = normalize_numbers(km)
    providers[idx] = p
    saved_trans[str(segments[idx].id)] = {"text_km": segments[idx].text_km, "provider": p, "source": segments[idx].source_text}
   trans_ckpt.write_text(json.dumps(saved_trans, ensure_ascii=False, indent=2), encoding="utf-8")
  else:
   counters["translate_computed"] = 0
   log.info("Job %s: Translation checkpoint hit; skipped %d segments", job_id, len(segments))
  stage_times["translate"] = round(time.time() - t_translate, 3)
  if opts.get("review_before_tts"):
   update_job(job_id,status="awaiting_review",stage="review",progress=50,segments=[s.__dict__ for s in segments],providers=providers); return
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  from app.render import render
  render(job_id,source,root,segments,duration,opts,transcript,providers,video,started,counters=counters,stage_times=stage_times)
 except Exception as exc:
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  log.exception("job %s failed",job_id); payload=failure_error(exc)
  if 'segments' in locals(): payload['segments']=[s.__dict__ for s in segments]
  fail_prog = min((get_job(job_id) or {}).get("progress", 0), 99)
  update_job(job_id,status="failed",stage="failed",progress=fail_prog,error={k:v for k,v in payload.items() if k!='segments'},segments=[s.__dict__ for s in segments] if 'segments' in locals() else None)
