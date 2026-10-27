import asyncio, errno, hashlib, json, logging, math, os, re, shutil, time, uuid
log = logging.getLogger("dubber.api")
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse, Response
from app.pipeline import failure_error, process_video, probe, kill_job_process
from app.storage import (
    delete_job, get_idempotent_job, get_job, list_jobs, recover_jobs,
    set_idempotent_job, set_job, update_job
)
from app.auth import get_current_token, get_current_user, get_user_id_from_request, check_queue_capacity, check_daily_quota
from app.i18n import get_km

from app.config import UPLOAD_DIR, OUTPUT_DIR, BASE_DIR
from app.config import MAX_UPLOAD_MB, MAX_UPLOAD_BYTES, MAX_VIDEO_SECONDS, SUPPORTED_EXTENSIONS

router = APIRouter(dependencies=[Depends(get_current_token)])
public_router = APIRouter()
UPLOADS = UPLOAD_DIR
OUTPUTS = OUTPUT_DIR
ROOT = BASE_DIR  # project root — path-traversal security boundary for downloads
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

POOL = ThreadPoolExecutor(max_workers=int(os.getenv("MAX_CONCURRENT_JOBS", "1")))

def _is_safe_download_path(p: Path) -> bool:
    """Security boundary check: ensure path is within ROOT, UPLOADS, or OUTPUTS."""
    sp = str(p.resolve())
    return (
        sp.startswith(str(ROOT.resolve()))
        or sp.startswith(str(UPLOADS.resolve()))
        or sp.startswith(str(OUTPUTS.resolve()))
    )


def safe_filename(name: str) -> str:
    base = Path(name or 'video.mp4').name
    clean = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', base)
    if not clean or clean.startswith('.'):
        clean = 'video.mp4'
    return clean[:120]

def worker(jid, path, opts):
    update_job(jid, started_at=time.time())
    process_video(jid, path, opts)

def resume_render_worker(jid, *render_args):
    """Render reviewed segments and persist failures like the normal worker."""
    try:
        from app.render import render
        render(jid, *render_args)
    except Exception as exc:
        if (get_job(jid) or {}).get('status') == 'canceled':
            return
        log.exception("reviewed job %s failed", jid)
        payload = failure_error(exc)
        progress = min((get_job(jid) or {}).get('progress', 0), 99)
        update_job(jid, status='failed', stage='failed', progress=progress, error=payload)

@router.post('/api/jobs', status_code=202)
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    source_lang: str = Form('auto'),
    voice: str = Form('km-KH-SreymomNeural'),
    keep_background: bool = Form(False),
    burn_subtitles: bool = Form(False),
    review_before_tts: bool = Form(False),
    idempotency_key: str | None = Header(None, alias='Idempotency-Key')
):
    user = get_current_user(request)
    user_id = user["user_id"]
    token = user.get("_token") or user_id  # backward compat
    check_queue_capacity()
    if idempotency_key:
        existing = get_idempotent_job(idempotency_key, user_id=user_id)
        if existing:
            return {'id': existing['id'], 'job_id': existing['id'], 'status': existing.get('status', 'queued')}
    if source_lang not in {'auto', 'zh', 'en'}:
        raise HTTPException(400, 'source_lang must be auto, zh, or en')
    if voice not in {'km-KH-SreymomNeural', 'km-KH-PisethNeural'}:
        raise HTTPException(400, 'Unsupported Khmer voice')

    fname = safe_filename(file.filename or 'video.mp4')
    ext = Path(fname).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, get_km('invalid_video'))

    free_disk = shutil.disk_usage(UPLOADS).free
    if free_disk < 20 * 1024 * 1024:
        raise HTTPException(507, get_km('disk_space_low'))

    jid = uuid.uuid4().hex
    folder = UPLOADS / jid
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / fname
    size = 0
    try:
        with path.open('wb') as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, get_km('upload_too_large'))
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, get_km('file_empty'))
        meta = await asyncio.to_thread(probe, path)
        dur = float(meta.get('format', {}).get('duration', 0))
        check_daily_quota(user_id, dur / 60.0)
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        err_msg = str(exc).lower()
        if "duration" in err_msg:
            raise HTTPException(400, get_km('duration_too_long')) from exc
        if "exceeds" in err_msg and "limit" in err_msg or "upload limit" in err_msg or "upload_too_large" in err_msg or "500 mb" in err_msg:
            raise HTTPException(413, get_km('upload_too_large')) from exc
        if "disk" in err_msg or (isinstance(exc, OSError) and getattr(exc, "errno", None) in (errno.ENOSPC, 28)):
            raise HTTPException(507, get_km('disk_space_low')) from exc
        raise HTTPException(400, get_km('invalid_video')) from exc

    opts = {
        'source_lang': source_lang,
        'voice': voice,
        'keep_background': keep_background,
        'burn_subtitles': burn_subtitles,
        'review_before_tts': review_before_tts
    }
    job_data = {
        'id': jid,
        'owner_token': token,
        'user_id': user_id,
        'status': 'queued',
        'stage': 'queued',
        'progress': 0,
        'filename': path.name,
        'source_path': str(path),
        'options': opts
    }
    set_job(jid, job_data)
    if idempotency_key:
        set_idempotent_job(idempotency_key, jid)
    POOL.submit(worker, jid, str(path), opts)
    return {'id': jid, 'job_id': jid, 'status': 'queued'}

@router.get('/api/jobs')
def jobs(request: Request):
    user_id = get_user_id_from_request(request)
    return list_jobs(user_id=user_id)

@router.get('/api/jobs/{jid}')
def status(jid: str, request: Request):
    user_id = get_user_id_from_request(request)
    job = get_job(jid, user_id=user_id)
    if not job:
        raise HTTPException(404, 'Job not found')
    return job

@router.get('/api/jobs/{jid}/events')
async def events(jid: str, request: Request):
    user_id = get_user_id_from_request(request)
    if not get_job(jid, user_id=user_id):
        raise HTTPException(404, 'Job not found')

    async def gen():
        previous = None
        for sec in range(86400):
            if await request.is_disconnected():
                break
            item = get_job(jid, user_id=user_id)
            if item != previous:
                yield 'data: ' + json.dumps(item, ensure_ascii=False) + '\n\n'
                previous = item
            elif sec % 15 == 0:
                yield ': keepalive\n\n'
            if item and item.get('status') in {'completed', 'failed', 'canceled'}:
                break
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type='text/event-stream')

@router.get('/api/jobs/{jid}/segments')
def segments(jid: str, request: Request):
    user_id = get_user_id_from_request(request)
    item = get_job(jid, user_id=user_id)
    if not item:
        raise HTTPException(404, 'Job not found')
    return item.get('segments', [])

@router.put('/api/jobs/{jid}/segments')
def edit_segments(jid: str, payload: list[dict], request: Request):
    user_id = get_user_id_from_request(request)
    if not get_job(jid, user_id=user_id):
        raise HTTPException(404, 'Job not found')
    update_job(jid, segments=payload)
    return {'ok': True}

@router.post('/api/jobs/{jid}/resume')
def resume(jid: str, request: Request):
    user_id = get_user_id_from_request(request)
    item = get_job(jid, user_id=user_id)
    if not item or item.get('status') != 'awaiting_review':
        raise HTTPException(409, 'Job is not awaiting review')
    from app.segments import Segment
    src = Path(item['source_path'])
    meta = probe(src)
    video = next(x for x in meta['streams'] if x['codec_type'] == 'video')
    info = type('Info', (), {'language': item['detected_language'], 'language_probability': 1.0})()
    # Clear a prior timing-review diagnostic before re-rendering edited text.
    update_job(jid, status='running', stage='voice', error=None, timing_review=None)
    POOL.submit(
        resume_render_worker, jid, src, src.parent,
        [Segment(**x) for x in item['segments']],
        float(meta['format']['duration']),
        item['options'], info, item.get('providers', []),
        video, time.time()
    )
    return {'status': 'running'}

@router.post('/api/jobs/{jid}/cancel')
def cancel(jid: str, request: Request):
    user_id = get_user_id_from_request(request)
    item = get_job(jid, user_id=user_id)
    if not item:
        raise HTTPException(404, 'Job not found')
    update_job(jid, status='canceled', stage='canceled')
    kill_job_process(jid)
    src = item.get('source_path')
    if src:
        shutil.rmtree(Path(src).parent, ignore_errors=True)
    return {'status': 'canceled'}

@router.delete('/api/jobs/{jid}')
def remove(jid: str, request: Request):
    user_id = get_user_id_from_request(request)
    item = get_job(jid, user_id=user_id)
    if not item:
        raise HTTPException(404, 'Job not found')
    delete_job(jid)
    shutil.rmtree(Path(item['source_path']).parent, ignore_errors=True)
    shutil.rmtree(OUTPUTS / jid, ignore_errors=True)
    return {'deleted': True}

@router.get('/api/jobs/{jid}/download/{kind}')
def download(jid: str, kind: str, request: Request):
    if not re.match(r'^[a-f0-9]{32}$', jid):
        raise HTTPException(400, 'Invalid job ID format')
    if kind not in {'video', 'audio', 'srt', 'vtt', 'json', 'source'}:
        raise HTTPException(400, 'Invalid download kind')

    user_id = get_user_id_from_request(request)
    item = get_job(jid, user_id=user_id)
    if not item:
        raise HTTPException(404, 'Job not found')
    outputs = item.get('outputs', {})

    if kind == 'source':
        path = Path(item['source_path']).resolve()
        if not path.is_file() or not _is_safe_download_path(path):
            raise HTTPException(404, 'Source video missing')
        return FileResponse(path, media_type='video/mp4', filename=path.name)

    if not outputs.get(kind):
        raise HTTPException(404, 'Output unavailable')

    target_name = Path(outputs[kind]).name
    path = (Path(item['source_path']).parent / target_name).resolve()
    if not path.is_file() or not _is_safe_download_path(path):
        raise HTTPException(404, 'Output missing')

    typ = {
        'video': 'video/mp4',
        'audio': 'audio/wav',
        'srt': 'application/x-subrip',
        'vtt': 'text/vtt',
        'json': 'application/json'
    }[kind]
    return FileResponse(path, media_type=typ, filename=path.name)

@public_router.get('/api/voices')
def voices():
    return [
        {'id': 'km-KH-SreymomNeural', 'name': 'Sreymom', 'gender': 'female'},
        {'id': 'km-KH-PisethNeural', 'name': 'Piseth', 'gender': 'male'}
    ]

@public_router.post('/api/tts/preview')
def preview(payload: dict):
    from app.providers import synthesize_khmer
    from app.i18n import get_km
    voice = payload.get('voice', 'km-KH-SreymomNeural')
    if voice not in {'km-KH-SreymomNeural', 'km-KH-PisethNeural'}:
        raise HTTPException(400, 'Unsupported Khmer voice')
    raw_text = str(payload.get('text') or '').strip()
    text = raw_text if raw_text and any(c.isalnum() for c in raw_text) else get_km('default_preview_text')
    text = text[:200]
    path = UPLOADS / '_preview' / f'{uuid.uuid4().hex}.wav'
    path.parent.mkdir(exist_ok=True)
    try:
        provider = synthesize_khmer(text, voice, path, 2.0)
        data = path.read_bytes()
    except Exception as exc:
        raise HTTPException(503, str(exc)[:400]) from exc
    finally:
        path.unlink(missing_ok=True)
    return Response(content=data, media_type='audio/wav', headers={'X-TTS-Provider': provider})

CHUNK_DIR = UPLOADS / '_chunks'
CHUNK_DIR.mkdir(exist_ok=True)

@router.post('/api/upload/init')
async def upload_init(payload: dict, request: Request):
    user = get_current_user(request)
    user_id = user["user_id"]
    token = user.get("_token") or user_id
    check_queue_capacity()
    raw_name = str(payload.get('filename') or 'video.mp4')
    if '..' in raw_name or '/' in raw_name or '\\' in raw_name:
        raise HTTPException(400, 'Invalid filename')
    fname = safe_filename(raw_name)
    ext = Path(fname).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, get_km('invalid_video'))
    total_size = int(payload.get('total_size', 0))
    chunk_size = int(payload.get('chunk_size', 5 * 1024 * 1024))
    if total_size <= 0:
        raise HTTPException(400, get_km('file_empty'))
    if total_size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, get_km('upload_too_large'))
    needed = total_size * 2
    free = shutil.disk_usage(UPLOADS).free
    if free < needed:
        raise HTTPException(507, get_km('disk_space_low'))
    uid = uuid.uuid4().hex
    u_folder = CHUNK_DIR / uid
    u_folder.mkdir(parents=True, exist_ok=True)
    total_chunks = max(1, math.ceil(total_size / chunk_size))
    meta = {
        'upload_id': uid,
        'owner_token': token,
        'user_id': user_id,
        'filename': fname,
        'total_size': total_size,
        'chunk_size': chunk_size,
        'total_chunks': total_chunks,
        'created_at': time.time()
    }
    (u_folder / 'meta.json').write_text(json.dumps(meta), encoding='utf-8')
    return meta

@router.put('/api/upload/{uid}/chunk')
async def upload_chunk(
    uid: str,
    request: Request,
    x_chunk_index: int = Header(..., alias='X-Chunk-Index'),
    x_chunk_checksum: str | None = Header(None, alias='X-Chunk-Checksum')
):
    user = get_current_user(request)
    user_id = user["user_id"]
    token = user.get("_token") or user_id
    u_folder = CHUNK_DIR / uid
    meta_file = u_folder / 'meta.json'
    if not meta_file.exists():
        raise HTTPException(404, 'Upload session not found')
    meta = json.loads(meta_file.read_text(encoding='utf-8'))
    # Check ownership by user_id first, fall back to owner_token
    meta_uid = meta.get('user_id')
    meta_token = meta.get('owner_token')
    if meta_uid:
        if meta_uid != user_id:
            raise HTTPException(404, 'Upload session not found')
    elif meta_token and meta_token != token:
        raise HTTPException(404, 'Upload session not found')
    if x_chunk_index < 0 or x_chunk_index >= meta['total_chunks']:
        raise HTTPException(400, f'Invalid chunk index {x_chunk_index}')
    body = await request.body()
    current_part = u_folder / f'{x_chunk_index:06d}.part'
    existing_this = current_part.stat().st_size if current_part.exists() else 0
    existing_other = sum(p.stat().st_size for p in u_folder.glob('*.part')) - existing_this
    if existing_other + len(body) > MAX_UPLOAD_BYTES:
        shutil.rmtree(u_folder, ignore_errors=True)
        raise HTTPException(413, get_km('upload_too_large'))
    if x_chunk_checksum:
        c_hash = hashlib.sha256(body).hexdigest()
        if c_hash.lower() != x_chunk_checksum.lower():
            raise HTTPException(400, 'Chunk checksum mismatch')
    current_part.write_bytes(body)
    received = [int(p.stem) for p in u_folder.glob('*.part')]
    return {
        'upload_id': uid,
        'chunk_index': x_chunk_index,
        'chunks_received': len(received),
        'total_chunks': meta['total_chunks']
    }

@router.get('/api/upload/{uid}/status')
def upload_status(uid: str, request: Request):
    user = get_current_user(request)
    user_id = user["user_id"]
    token = user.get("_token") or user_id
    u_folder = CHUNK_DIR / uid
    meta_file = u_folder / 'meta.json'
    if not meta_file.exists():
        raise HTTPException(404, 'Upload session not found')
    meta = json.loads(meta_file.read_text(encoding='utf-8'))
    meta_uid = meta.get('user_id')
    meta_token = meta.get('owner_token')
    if meta_uid:
        if meta_uid != user_id:
            raise HTTPException(404, 'Upload session not found')
    elif meta_token and meta_token != token:
        raise HTTPException(404, 'Upload session not found')
    received = sorted(int(p.stem) for p in u_folder.glob('*.part'))
    return {
        'upload_id': uid,
        'filename': meta['filename'],
        'total_size': meta['total_size'],
        'chunk_size': meta['chunk_size'],
        'total_chunks': meta['total_chunks'],
        'received_chunks': received,
        'complete': len(received) == meta['total_chunks']
    }

@router.post('/api/upload/{uid}/complete')
async def upload_complete(uid: str, request: Request, payload: dict | None = None):
    user = get_current_user(request)
    user_id = user["user_id"]
    token = user.get("_token") or user_id
    check_queue_capacity()
    opts_in = payload or {}
    u_folder = CHUNK_DIR / uid
    meta_file = u_folder / 'meta.json'
    if not meta_file.exists():
        raise HTTPException(404, 'Upload session not found')
    meta = json.loads(meta_file.read_text(encoding='utf-8'))
    meta_uid = meta.get('user_id')
    meta_token = meta.get('owner_token')
    if meta_uid:
        if meta_uid != user_id:
            raise HTTPException(404, 'Upload session not found')
    elif meta_token and meta_token != token:
        raise HTTPException(404, 'Upload session not found')
    parts = sorted(u_folder.glob('*.part'))
    if len(parts) != meta['total_chunks']:
        raise HTTPException(400, f'Upload incomplete: {len(parts)}/{meta["total_chunks"]} chunks received')

    idempotency_key = opts_in.get('idempotency_key')
    if idempotency_key:
        existing = get_idempotent_job(idempotency_key, user_id=user_id)
        if existing:
            shutil.rmtree(u_folder, ignore_errors=True)
            return {'id': existing['id'], 'job_id': existing['id'], 'status': existing.get('status', 'queued')}

    source_lang = opts_in.get('source_lang', 'auto')
    voice = opts_in.get('voice', 'km-KH-SreymomNeural')
    keep_background = bool(opts_in.get('keep_background', False))
    burn_subtitles = bool(opts_in.get('burn_subtitles', False))
    review_before_tts = bool(opts_in.get('review_before_tts', False))
    if source_lang not in {'auto', 'zh', 'en'}:
        raise HTTPException(400, 'source_lang must be auto, zh, or en')
    if voice not in {'km-KH-SreymomNeural', 'km-KH-PisethNeural'}:
        raise HTTPException(400, 'Unsupported Khmer voice')

    jid = uuid.uuid4().hex
    job_folder = UPLOADS / jid
    job_folder.mkdir(parents=True, exist_ok=True)
    target_path = job_folder / meta['filename']
    try:
        with target_path.open('wb') as out_f:
            for p in parts:
                with p.open('rb') as in_f:
                    shutil.copyfileobj(in_f, out_f)
            out_f.flush()
            try:
                os.fsync(out_f.fileno())
            except Exception:
                pass
        act_sz = target_path.stat().st_size
        for _ in range(10):
            if act_sz == meta['total_size']:
                break
            time.sleep(0.05)
            act_sz = target_path.stat().st_size
        if act_sz != meta['total_size']:
            raise ValueError(f"Assembled file size ({act_sz}) does not match expected total_size ({meta['total_size']})")
        if target_path.stat().st_size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, get_km('upload_too_large'))
        probe_data = await asyncio.to_thread(probe, target_path)
        dur = float(probe_data.get('format', {}).get('duration', 0))
        check_daily_quota(user_id, dur / 60.0)
    except HTTPException:
        shutil.rmtree(job_folder, ignore_errors=True)
        shutil.rmtree(u_folder, ignore_errors=True)
        raise
    except Exception as exc:
        log.exception("upload_complete failed for %s", uid)
        shutil.rmtree(job_folder, ignore_errors=True)
        shutil.rmtree(u_folder, ignore_errors=True)
        err_msg = str(exc).lower()
        if "duration" in err_msg:
            raise HTTPException(400, get_km('duration_too_long')) from exc
        if "exceeds" in err_msg and "limit" in err_msg or "upload limit" in err_msg or "upload_too_large" in err_msg or "500 mb" in err_msg:
            raise HTTPException(413, get_km('upload_too_large')) from exc
        if "disk" in err_msg or (isinstance(exc, OSError) and getattr(exc, "errno", None) in (errno.ENOSPC, 28)):
            raise HTTPException(507, get_km('disk_space_low')) from exc
        raise HTTPException(400, get_km('invalid_video')) from exc

    shutil.rmtree(u_folder, ignore_errors=True)
    opts = {
        'source_lang': source_lang,
        'voice': voice,
        'keep_background': keep_background,
        'burn_subtitles': burn_subtitles,
        'review_before_tts': review_before_tts
    }
    job_data = {
        'id': jid,
        'owner_token': token,
        'user_id': user_id,
        'status': 'queued',
        'stage': 'queued',
        'progress': 0,
        'filename': target_path.name,
        'source_path': str(target_path),
        'options': opts
    }
    set_job(jid, job_data)
    if idempotency_key:
        set_idempotent_job(idempotency_key, jid)
    POOL.submit(worker, jid, str(target_path), opts)
    return {'id': jid, 'job_id': jid, 'status': 'queued'}
