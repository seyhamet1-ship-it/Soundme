from __future__ import annotations

import os
import platform
import shutil
import stat
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


def _load_project_env() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    search_roots: list[Path] = [project_root, Path.cwd()]
    for parent in list(Path.cwd().parents):
        search_roots.append(parent)
    seen: set[Path] = set()
    for root in search_roots:
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        env_path = resolved / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
    return project_root


BASE_DIR = _load_project_env()
TOOLS_DIR = BASE_DIR / "tools"
TOOLS_DIR.mkdir(parents=True, exist_ok=True)

# Always prefer the project-local tools directory so subprocesses can find ffmpeg/ffprobe.
os.environ["PATH"] = str(TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")

UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
FRONTEND_DIR = BASE_DIR / "frontend"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
FRONTEND_DIR.mkdir(exist_ok=True, parents=True)

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_VIDEO_SECONDS = int(os.getenv("MAX_DURATION_MIN", "30")) * 60
SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


TRANSLATION_PROVIDER = os.getenv("TRANSLATION_PROVIDER", "local").strip().lower() or "local"
TRANSLATION_FALLBACK_PROVIDER = os.getenv("TRANSLATION_FALLBACK_PROVIDER", "google").strip().lower() or "google"
TRANSLATION_LOCAL_MODEL = os.getenv("TRANSLATION_LOCAL_MODEL", "facebook/nllb-200-distilled-600M").strip() or "facebook/nllb-200-distilled-600M"
TRANSLATION_OFFLINE = _bool_env("TRANSLATION_OFFLINE", False)
TRANSLATION_MAX_RETRIES = int(os.getenv("TRANSLATION_MAX_RETRIES", "5"))
TRANSLATION_MIN_DELAY = float(os.getenv("TRANSLATION_MIN_DELAY", "1.5"))
TRANSLATION_BACKOFF_BASE = float(os.getenv("TRANSLATION_BACKOFF_BASE", "2"))
TRANSLATION_TIMEOUT = float(os.getenv("TRANSLATION_TIMEOUT", "30"))

LANGUAGE_MAP = {
    "en": {"label": "English", "tts_voice": "en-US-JennyNeural"},
    "fr": {"label": "French", "tts_voice": "fr-FR-HenriNeural"},
    "es": {"label": "Spanish", "tts_voice": "es-ES-ElviraNeural"},
    "de": {"label": "German", "tts_voice": "de-DE-KatjaNeural"},
    "it": {"label": "Italian", "tts_voice": "it-IT-ElsaNeural"},
    "pt": {"label": "Portuguese", "tts_voice": "pt-BR-FranciscaNeural"},
    "ja": {"label": "Japanese", "tts_voice": "ja-JP-NanamiNeural"},
    "ko": {"label": "Korean", "tts_voice": "ko-KR-SunHiNeural"},
    "vi": {"label": "Vietnamese", "tts_voice": "vi-VN-NamMinhNeural"},
    "km": {"label": "Khmer", "tts_voice": "km-KH-PisethNeural"},
    "th": {"label": "Thai", "tts_voice": "th-TH-PremwadeeNeural"},
    "ar": {"label": "Arabic", "tts_voice": "ar-SA-ZaydNeural"},
    "ru": {"label": "Russian", "tts_voice": "ru-RU-SvetlanaNeural"},
    "zh": {"label": "Chinese", "tts_voice": "zh-CN-XiaoxiaoNeural"},
}


def _ffmpeg_filename() -> str:
    return "ffmpeg.exe" if platform.system().lower() == "windows" else "ffmpeg"


def _ffprobe_filename() -> str:
    return "ffprobe.exe" if platform.system().lower() == "windows" else "ffprobe"


def _local_candidates(name: str) -> list[Path]:
    candidates: list[Path] = []
    for root in [TOOLS_DIR, BASE_DIR / "bin", BASE_DIR / ".bin"]:
        if root.exists():
            candidates.extend(root.rglob(name))
    return candidates


def _make_executable(path: Path) -> None:
    if not path.exists():
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _download_static_ffmpeg() -> tuple[Path, Path]:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        if machine in {"amd64", "x86_64", "x64"}:
            url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
        else:
            raise RuntimeError(f"Unsupported Windows architecture for FFmpeg download: {machine}")
    elif system == "linux":
        if machine in {"amd64", "x86_64", "x64"}:
            url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linux64-gpl.tar.xz"
        elif machine in {"arm64", "aarch64"}:
            url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linuxarm64-gpl.tar.xz"
        else:
            raise RuntimeError(f"Unsupported Linux architecture for FFmpeg download: {machine}")
    else:
        raise RuntimeError(f"Automatic FFmpeg download is not supported on this platform: {system}")

    archive_name = Path(urlparse(url).path).name
    archive_path = TOOLS_DIR / archive_name

    try:
        urllib.request.urlretrieve(url, str(archive_path))
    except Exception as exc:  # pragma: no cover - network/download path
        raise RuntimeError(f"Failed to download FFmpeg from {url}: {exc}") from exc

    try:
        if archive_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(TOOLS_DIR)
        elif archive_path.suffix.lower() in {".xz", ".tar"}:
            with tarfile.open(archive_path, "r:*") as tf:
                tf.extractall(TOOLS_DIR)
        else:
            raise RuntimeError(f"Unsupported FFmpeg archive type: {archive_path.suffix}")
    finally:
        if archive_path.exists():
            archive_path.unlink(missing_ok=True)

    ffmpeg_candidates = _local_candidates(_ffmpeg_filename())
    ffprobe_candidates = _local_candidates(_ffprobe_filename())

    if not ffmpeg_candidates:
        raise RuntimeError("Downloaded FFmpeg archive did not contain ffmpeg.exe/ffmpeg.")
    if not ffprobe_candidates:
        raise RuntimeError("Downloaded FFmpeg archive did not contain ffprobe.exe/ffprobe.")

    ffmpeg_path = sorted(ffmpeg_candidates, key=lambda p: len(p.parts))[0].resolve()
    ffprobe_path = sorted(ffprobe_candidates, key=lambda p: len(p.parts))[0].resolve()

    _make_executable(ffmpeg_path)
    _make_executable(ffprobe_path)

    os.environ["PATH"] = str(TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")
    return ffmpeg_path, ffprobe_path


def ensure_ffmpeg() -> Path:
    ffmpeg_path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if ffmpeg_path:
        resolved = Path(ffmpeg_path).resolve()
        os.environ["PATH"] = str(TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")
        return resolved

    local_candidates = _local_candidates(_ffmpeg_filename())
    if local_candidates:
        resolved = sorted(local_candidates, key=lambda p: len(p.parts))[0].resolve()
        os.environ["PATH"] = str(TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")
        return resolved

    ffmpeg_bin, _ = _download_static_ffmpeg()
    os.environ["PATH"] = str(TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")
    return ffmpeg_bin


def ensure_ffprobe() -> Path:
    ffprobe_path = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if ffprobe_path:
        resolved = Path(ffprobe_path).resolve()
        os.environ["PATH"] = str(TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")
        return resolved

    local_candidates = _local_candidates(_ffprobe_filename())
    if local_candidates:
        resolved = sorted(local_candidates, key=lambda p: len(p.parts))[0].resolve()
        os.environ["PATH"] = str(TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")
        return resolved

    _, ffprobe_bin = _download_static_ffmpeg()
    os.environ["PATH"] = str(TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")
    return ffprobe_bin