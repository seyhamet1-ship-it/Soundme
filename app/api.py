import asyncio,hashlib,json,logging,math,os,shutil,time,uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import APIRouter,File,Form,Header,HTTPException,Request,UploadFile
from fastapi.responses import FileResponse,StreamingResponse,Response
from app.pipeline import process_video,probe,kill_job_process
from app.storage import delete_job,get_idempotent_job,get_job,list_jobs,recover_jobs,set_idempotent_job,set_job,update_job
from app.i18n import get_km
router=APIRouter(); ROOT=Path(__file__).resolve().parents[1]; UPLOADS=ROOT/'uploads'; OUTPUTS=ROOT/'outputs'; UPLOADS.mkdir(exist_ok=True); OUTPUTS.mkdir(exist_ok=True)
POOL=ThreadPoolExecutor(max_workers=2)
from app.config import MAX_UPLOAD_MB, MAX_UPLOAD_BYTES, MAX_VIDEO_SECONDS
def worker(jid,path,opts): update_job(jid,started_at=time.time()); process_video(jid,path,opts)
@router.post('/api/jobs',status_code=202)
async def create_job(file:UploadFile=File(...),source_lang:str=Form('auto'),voice:str=Form('km-KH-SreymomNeural'),keep_background:bool=Form(False),burn_subtitles:bool=Form(False),review_before_tts:bool=Form(False),idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
 if idempotency_key:
  existing=get_idempotent_job(idempotency_key)
  if existing:
   return {'id':existing['id'],'job_id':existing['id'],'status':existing.get('status','queued')}
 if source_lang not in {'auto','zh','en'}: raise HTTPException(400,'source_lang must be auto, zh, or en')
 if voice not in {'km-KH-SreymomNeural','km-KH-PisethNeural'}: raise HTTPException(400,'Unsupported Khmer voice')
 jid=uuid.uuid4().hex; folder=UPLOADS/jid; folder.mkdir(parents=True,exist_ok=True); path=folder/(Path(file.filename or 'video.mp4').name[:120]); size=0
 try:
  with path.open('wb') as out:
   while chunk:=await file.read(1024*1024):
    size+=len(chunk)
    if size>MAX_UPLOAD_BYTES: raise HTTPException(413,get_km('upload_too_large'))
    out.write(chunk)
  if size==0: raise HTTPException(400,get_km('file_empty'))
  await asyncio.to_thread(probe,path)
 except HTTPException: shutil.rmtree(folder,ignore_errors=True); raise
 except Exception as exc:
  shutil.rmtree(folder,ignore_errors=True)
  err_msg = str(exc).lower()
  if "duration" in err_msg: raise HTTPException(400, get_km('duration_too_long')) from exc
  if "exceeds the 500 mb" in err_msg or "upload limit" in err_msg: raise HTTPException(413, get_km('upload_too_large')) from exc
  if "disk" in err_msg: raise HTTPException(400, get_km('disk_space_low')) from exc
  raise HTTPException(400,get_km('invalid_video')) from exc
 opts={'source_lang':source_lang,'voice':voice,'keep_background':keep_background,'burn_subtitles':burn_subtitles,'review_before_tts':review_before_tts}
 job_data={'id':jid,'status':'queued','stage':'queued','progress':0,'filename':path.name,'source_path':str(path),'options':opts}
 set_job(jid,job_data)
 if idempotency_key: set_idempotent_job(idempotency_key,jid)
 POOL.submit(worker,jid,str(path),opts); return {'id':jid,'job_id':jid,'status':'queued'}
@router.get('/api/jobs')
def jobs(): return list_jobs()
@router.get('/api/jobs/{jid}')
def status(jid:str):
 job=get_job(jid)
 if not job: raise HTTPException(404,'Job not found')
 return job
@router.get('/api/jobs/{jid}/events')
async def events(jid:str):
 if not get_job(jid): raise HTTPException(404,'Job not found')
 async def gen():
  previous=None
  for _ in range(3600):
   item=get_job(jid)
   if item!=previous: yield 'data: '+json.dumps(item,ensure_ascii=False)+'\n\n'; previous=item
   if item and item.get('status') in {'completed','failed','canceled'}: break
   await asyncio.sleep(1)
 return StreamingResponse(gen(),media_type='text/event-stream')
@router.get('/api/jobs/{jid}/segments')
def segments(jid:str):
 item=get_job(jid)
 if not item: raise HTTPException(404,'Job not found')
 return item.get('segments',[])
@router.put('/api/jobs/{jid}/segments')
def edit_segments(jid:str,payload:list[dict]):
 if not get_job(jid): raise HTTPException(404,'Job not found')
 update_job(jid,segments=payload); return {'ok':True}
@router.post('/api/jobs/{jid}/resume')
def resume(jid:str):
 item=get_job(jid)
 if not item or item.get('status')!='awaiting_review': raise HTTPException(409,'Job is not awaiting review')
 from app.render import render
 from app.segments import Segment
 src=Path(item['source_path']); meta=probe(src); video=next(x for x in meta['streams'] if x['codec_type']=='video'); info=type('Info',(),{'language':item['detected_language'],'language_probability':1.0})()
 update_job(jid,status='running',stage='voice'); POOL.submit(render,jid,src,src.parent,[Segment(**x) for x in item['segments']],float(meta['format']['duration']),item['options'],info,item.get('providers',[]),video,time.time()); return {'status':'running'}
@router.post('/api/jobs/{jid}/cancel')
def cancel(jid:str):
 item=get_job(jid)
 if not item: raise HTTPException(404,'Job not found')
 update_job(jid,status='canceled',stage='canceled')
 kill_job_process(jid)
 src=item.get('source_path')
 if src: shutil.rmtree(Path(src).parent,ignore_errors=True)
 return {'status':'canceled'}
@router.delete('/api/jobs/{jid}')
def remove(jid:str):
 item=get_job(jid)
 if not item: raise HTTPException(404,'Job not found')
 delete_job(jid); shutil.rmtree(Path(item['source_path']).parent,ignore_errors=True); shutil.rmtree(OUTPUTS/jid,ignore_errors=True); return {'deleted':True}
@router.get('/api/jobs/{jid}/download/{kind}')
def download(jid:str,kind:str):
 item=get_job(jid); outputs=item.get('outputs',{}) if item else {}
 if not item: raise HTTPException(404,'Job not found')
 if kind == 'source':
  path = Path(item['source_path'])
  if not path.is_file(): raise HTTPException(404,'Source video missing')
  return FileResponse(path,media_type='video/mp4',filename=path.name)
 if kind not in {'video','audio','srt','vtt','json'} or not outputs.get(kind): raise HTTPException(404,'Output unavailable')
 path=Path(item['source_path']).parent/outputs[kind]
 if not path.is_file(): raise HTTPException(404,'Output missing')
 typ={'video':'video/mp4','audio':'audio/wav','srt':'application/x-subrip','vtt':'text/vtt','json':'application/json'}[kind]; return FileResponse(path,media_type=typ,filename=path.name)
@router.get('/api/voices')
def voices(): return [{'id':'km-KH-SreymomNeural','name':'Sreymom','gender':'female'},{'id':'km-KH-PisethNeural','name':'Piseth','gender':'male'}]
@router.post('/api/tts/preview')
def preview(payload:dict):
 from app.providers import synthesize_khmer
 from app.i18n import get_km
 voice=payload.get('voice','km-KH-SreymomNeural')
 if voice not in {'km-KH-SreymomNeural','km-KH-PisethNeural'}: raise HTTPException(400,'Unsupported Khmer voice')
 raw_text=str(payload.get('text') or '').strip()
 text=raw_text if raw_text and any(c.isalnum() for c in raw_text) else get_km('default_preview_text')
 text=text[:200]
 path=UPLOADS/'_preview'/f'{uuid.uuid4().hex}.wav'; path.parent.mkdir(exist_ok=True)
 try:
  provider=synthesize_khmer(text,voice,path,2.0); data=path.read_bytes()
 except Exception as exc:
  raise HTTPException(503,str(exc)[:400]) from exc
 finally:
  path.unlink(missing_ok=True)
 return Response(content=data,media_type='audio/wav',headers={'X-TTS-Provider':provider})

CHUNK_DIR = UPLOADS / '_chunks'
CHUNK_DIR.mkdir(exist_ok=True)

@router.post('/api/upload/init')
async def upload_init(payload: dict):
 fname = Path(payload.get('filename') or 'video.mp4').name[:120]
 total_size = int(payload.get('total_size', 0))
 chunk_size = int(payload.get('chunk_size', 5 * 1024 * 1024))
 if total_size <= 0: raise HTTPException(400, get_km('file_empty'))
 if total_size > MAX_UPLOAD_BYTES: raise HTTPException(413, get_km('upload_too_large'))
 needed = total_size * 3
 free = shutil.disk_usage(UPLOADS).free
 if free < needed: raise HTTPException(400, get_km('disk_space_low'))
 uid = uuid.uuid4().hex
 u_folder = CHUNK_DIR / uid
 u_folder.mkdir(parents=True, exist_ok=True)
 total_chunks = max(1, math.ceil(total_size / chunk_size))
 meta = {'upload_id': uid, 'filename': fname, 'total_size': total_size, 'chunk_size': chunk_size, 'total_chunks': total_chunks, 'created_at': time.time()}
 (u_folder / 'meta.json').write_text(json.dumps(meta), encoding='utf-8')
 return meta

@router.put('/api/upload/{uid}/chunk')
async def upload_chunk(uid: str, request: Request, x_chunk_index: int = Header(..., alias='X-Chunk-Index'), x_chunk_checksum: str | None = Header(None, alias='X-Chunk-Checksum')):
 u_folder = CHUNK_DIR / uid
 meta_file = u_folder / 'meta.json'
 if not meta_file.exists(): raise HTTPException(404, 'Upload session not found')
 meta = json.loads(meta_file.read_text(encoding='utf-8'))
 if x_chunk_index < 0 or x_chunk_index >= meta['total_chunks']: raise HTTPException(400, f'Invalid chunk index {x_chunk_index}')
 body = await request.body()
 # Check cumulative size including this chunk (client might send more bytes than declared or exceed limit)
 current_part = u_folder / f'{x_chunk_index:06d}.part'
 existing_this = current_part.stat().st_size if current_part.exists() else 0
 existing_other = sum(p.stat().st_size for p in u_folder.glob('*.part')) - existing_this
 if existing_other + len(body) > MAX_UPLOAD_BYTES:
  shutil.rmtree(u_folder, ignore_errors=True)
  raise HTTPException(413, get_km('upload_too_large'))
 if x_chunk_checksum:
  c_hash = hashlib.sha256(body).hexdigest()
  if c_hash.lower() != x_chunk_checksum.lower(): raise HTTPException(400, 'Chunk checksum mismatch')
 current_part.write_bytes(body)
 received = [int(p.stem) for p in u_folder.glob('*.part')]
 return {'upload_id': uid, 'chunk_index': x_chunk_index, 'chunks_received': len(received), 'total_chunks': meta['total_chunks']}

@router.get('/api/upload/{uid}/status')
def upload_status(uid: str):
 u_folder = CHUNK_DIR / uid
 meta_file = u_folder / 'meta.json'
 if not meta_file.exists(): raise HTTPException(404, 'Upload session not found')
 meta = json.loads(meta_file.read_text(encoding='utf-8'))
 received = sorted(int(p.stem) for p in u_folder.glob('*.part'))
 return {'upload_id': uid, 'filename': meta['filename'], 'total_size': meta['total_size'], 'chunk_size': meta['chunk_size'], 'total_chunks': meta['total_chunks'], 'received_chunks': received, 'complete': len(received) == meta['total_chunks']}

@router.post('/api/upload/{uid}/complete')
async def upload_complete(uid: str, payload: dict | None = None):
 opts_in = payload or {}
 u_folder = CHUNK_DIR / uid
 meta_file = u_folder / 'meta.json'
 if not meta_file.exists(): raise HTTPException(404, 'Upload session not found')
 meta = json.loads(meta_file.read_text(encoding='utf-8'))
 parts = sorted(u_folder.glob('*.part'))
 if len(parts) != meta['total_chunks']: raise HTTPException(400, f'Upload incomplete: {len(parts)}/{meta["total_chunks"]} chunks received')
 idempotency_key = opts_in.get('idempotency_key')
 if idempotency_key:
  existing = get_idempotent_job(idempotency_key)
  if existing:
   shutil.rmtree(u_folder, ignore_errors=True)
   return {'id': existing['id'], 'job_id': existing['id'], 'status': existing.get('status', 'queued')}
 source_lang = opts_in.get('source_lang', 'auto')
 voice = opts_in.get('voice', 'km-KH-SreymomNeural')
 keep_background = bool(opts_in.get('keep_background', False))
 burn_subtitles = bool(opts_in.get('burn_subtitles', False))
 review_before_tts = bool(opts_in.get('review_before_tts', False))
 if source_lang not in {'auto', 'zh', 'en'}: raise HTTPException(400, 'source_lang must be auto, zh, or en')
 if voice not in {'km-KH-SreymomNeural', 'km-KH-PisethNeural'}: raise HTTPException(400, 'Unsupported Khmer voice')
 jid = uuid.uuid4().hex
 job_folder = UPLOADS / jid
 job_folder.mkdir(parents=True, exist_ok=True)
 target_path = job_folder / meta['filename']
 try:
  with target_path.open('wb') as out_f:
   for p in parts:
    with p.open('rb') as in_f: shutil.copyfileobj(in_f, out_f)
  if target_path.stat().st_size != meta['total_size']: raise ValueError('Assembled file size does not match expected total_size')
  if target_path.stat().st_size > MAX_UPLOAD_BYTES:
   raise HTTPException(413, get_km('upload_too_large'))
  await asyncio.to_thread(probe, target_path)
 except HTTPException:
  shutil.rmtree(job_folder, ignore_errors=True); shutil.rmtree(u_folder, ignore_errors=True); raise
 except Exception as exc:
  shutil.rmtree(job_folder, ignore_errors=True); shutil.rmtree(u_folder, ignore_errors=True)
  err_msg = str(exc).lower()
  if "duration" in err_msg:
   raise HTTPException(400, get_km('duration_too_long')) from exc
  if "exceeds the 500 mb" in err_msg or "upload limit" in err_msg:
   raise HTTPException(413, get_km('upload_too_large')) from exc
  if "disk" in err_msg:
   raise HTTPException(400, get_km('disk_space_low')) from exc
  raise HTTPException(400, get_km('invalid_video')) from exc
 shutil.rmtree(u_folder, ignore_errors=True)
 opts = {'source_lang': source_lang, 'voice': voice, 'keep_background': keep_background, 'burn_subtitles': burn_subtitles, 'review_before_tts': review_before_tts}
 job_data = {'id': jid, 'status': 'queued', 'stage': 'queued', 'progress': 0, 'filename': target_path.name, 'source_path': str(target_path), 'options': opts}
 set_job(jid, job_data)
 if idempotency_key: set_idempotent_job(idempotency_key, jid)
 POOL.submit(worker, jid, str(target_path), opts)
 return {'id': jid, 'job_id': jid, 'status': 'queued'}

