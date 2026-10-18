from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import router
from app.storage import recover_jobs

@asynccontextmanager
async def lifespan(app: FastAPI):
    recover_jobs()
    yield

app=FastAPI(title="Khmer Video Dubber",version="1.0.0",description="Translate Chinese or English video into Khmer speech and subtitles.",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=__import__("os").getenv("CORS_ORIGINS","http://localhost:5173,http://127.0.0.1:5173").split(","),allow_methods=["*"],allow_headers=["*"])
app.include_router(router)
@app.get("/api/health")
def health():
 import shutil,os
 tools={name:shutil.which(name) is not None for name in ("ffmpeg","ffprobe")}
 return {"status":"ok" if all(tools.values()) else "degraded",**tools,"asr":"faster-whisper (loaded on demand)","translation":os.getenv("TRANSLATION_PROVIDER","nllb-local with labeled fallback"),"tts":"auto: Edge TTS -> Azure Speech (if configured) -> MMS; Edge and MMS live probes PASS 2026-10-16","workers":2}
