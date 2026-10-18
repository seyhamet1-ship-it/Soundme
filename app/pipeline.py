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
 max_dur=float(os.getenv("MAX_DURATION_MIN","90"))*60
 if duration<=0 or duration>max_dur: raise ValueError("Video duration is invalid or exceeds the configured limit.")
 # Disk space pre-check: need ~3x input size for intermediate files
 import shutil as _shutil
 file_size=path.stat().st_size
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
 if "no audio track" in lower: return {"code":"NO_AUDIO_TRACK","message_en":message,"message_km":get_km("no_audio_track")}
 if "no speech was detected" in lower or "no speech detected" in lower: return {"code":"NO_SPEECH_DETECTED","message_en":message,"message_km":get_km("no_speech_detected")}
 if "no video stream" in lower: return {"code":"NO_VIDEO_STREAM","message_en":message,"message_km":get_km("no_video_stream")}
 if "invalid video" in lower: return {"code":"INVALID_VIDEO","message_en":message,"message_km":get_km("invalid_video")}
 if "file is empty" in lower or "empty" in lower: return {"code":"FILE_EMPTY","message_en":message,"message_km":get_km("file_empty")}
 return {"code":"PROCESSING_FAILED","message_en":message,"message_km":get_km("processing_failed")}
def process_video(job_id:str,source_path:str,options:dict[str,Any]|None=None)->None:
 opts=options or {}; root=Path(source_path).parent; source=Path(source_path); started=time.time()
 try:
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  update_job(job_id,status="running",stage="validate",progress=3); meta=probe(source); video=next(s for s in meta["streams"] if s["codec_type"]=="video")
  if not any(s["codec_type"]=="audio" for s in meta["streams"]): raise ValueError("This video has no audio track to transcribe.")
  duration=float(meta["format"]["duration"]); wav=root/"source.wav"
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  update_job(job_id,stage="extract",progress=8)
  run([FFMPEG,"-y","-i",str(source),"-map","0:a:0","-vn","-ac","1","-ar","16000","-c:a","pcm_s16le",str(wav)], job_id=job_id)
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  update_job(job_id,stage="transcribe",progress=18); transcript=WhisperASR(os.getenv("ASR_MODEL","small")).transcribe(wav,opts.get("source_lang","auto"))
  segments=[Segment(i+1,float(s.start),float(s.end),str(s.text).strip()) for i,s in enumerate(transcript.segments) if str(s.text).strip()]
  if not segments: raise ValueError("No speech was detected. Try a video with audible speech.")
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  update_job(job_id,detected_language=transcript.language,stage="translate",progress=38); providers=[]
  for s in segments:
   if (get_job(job_id) or {}).get("status") == "canceled":
    if root.exists(): shutil.rmtree(root, ignore_errors=True)
    return
   s.text_km,p=translate_khmer(s.source_text,transcript.language); s.text_km=normalize_numbers(s.text_km); providers.append(p)
  if opts.get("review_before_tts"):
   update_job(job_id,status="awaiting_review",stage="review",progress=50,segments=[s.__dict__ for s in segments],providers=providers); return
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  from app.render import render
  render(job_id,source,root,segments,duration,opts,transcript,providers,video,started)
 except Exception as exc:
  if (get_job(job_id) or {}).get("status") == "canceled":
   if root.exists(): shutil.rmtree(root, ignore_errors=True)
   return
  log.exception("job %s failed",job_id); payload=failure_error(exc)
  if 'segments' in locals(): payload['segments']=[s.__dict__ for s in segments]
  update_job(job_id,status="failed",stage="failed",progress=100,error={k:v for k,v in payload.items() if k!='segments'},segments=[s.__dict__ for s in segments] if 'segments' in locals() else None)
