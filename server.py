from __future__ import annotations

import json
import math
import os
import base64
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

try:
    import imageio_ffmpeg
except Exception:  # pragma: no cover - reported through /api/health
    imageio_ffmpeg = None

try:
    import yt_dlp
except Exception:  # pragma: no cover - reported through /api/health
    yt_dlp = None

try:
    import requests
except Exception:  # pragma: no cover - optional external worker client
    requests = None


ROOT = Path(__file__).resolve().parent
STORAGE = Path(os.getenv("HALALSTREAM_STORAGE_DIR", ROOT / "storage")).resolve()
JOBS_DIR = STORAGE / "jobs"
TOOLS_DIR = ROOT / "tools"
ASSETS_DIR = ROOT / "assets"
DEMUCS_MODEL = os.getenv("HALALSTREAM_DEMUCS_MODEL", "htdemucs")
DEMUCS_JOBS = int(os.getenv("HALALSTREAM_DEMUCS_JOBS", "1"))
DEMUCS_SEGMENT = int(float(os.getenv("HALALSTREAM_DEMUCS_SEGMENT", "0")))
DEMUCS_MAX_SEGMENT = int(float(os.getenv("HALALSTREAM_DEMUCS_MAX_SEGMENT", "7")))
DEMUCS_OVERLAP = float(os.getenv("HALALSTREAM_DEMUCS_OVERLAP", "0.75"))
# Dynamically adjust active jobs based on GPU availability
try:
    import torch
    HAS_GPU = torch.cuda.is_available()
except ImportError:
    HAS_GPU = False

default_max_jobs = "1"
MAX_ACTIVE_PROCESSING_JOBS = max(1, int(os.getenv("HALALSTREAM_MAX_ACTIVE_PROCESSING_JOBS", default_max_jobs)))
MUSIC_RATIO_THRESHOLD = float(os.getenv("HALALSTREAM_MUSIC_RATIO_THRESHOLD", "0.04"))
MUSIC_ABSOLUTE_RMS_THRESHOLD = float(os.getenv("HALALSTREAM_MUSIC_ABSOLUTE_RMS_THRESHOLD", "0.006"))
DEMUCS_SHIFTS = max(1, int(os.getenv("HALALSTREAM_DEMUCS_SHIFTS", "4" if HAS_GPU else "2")))
ALLOW_UNCHECKED_DIRECT = os.getenv("HALALSTREAM_ALLOW_UNCHECKED_DIRECT", "1").strip().lower() in {"1", "true", "yes"}
AUTO_PURIFY_ON_DETECTION = os.getenv("HALALSTREAM_AUTO_PURIFY_ON_DETECTION", "1").strip().lower() in {"1", "true", "yes"}
ESTIMATED_PROCESSING_SECONDS = max(60, int(os.getenv("HALALSTREAM_ESTIMATED_PROCESSING_SECONDS", "240")))
JOB_TTL_HOURS = max(1.0, float(os.getenv("HALALSTREAM_JOB_TTL_HOURS", "12")))
CLEANUP_INTERVAL_SECONDS = max(300, int(os.getenv("HALALSTREAM_CLEANUP_INTERVAL_SECONDS", "1800")))
MAX_UPLOAD_BYTES = max(1 * 1024 * 1024, int(os.getenv("HALALSTREAM_MAX_UPLOAD_BYTES", str(350 * 1024 * 1024))))
MAX_REMOTE_DOWNLOAD_BYTES = max(
    1 * 1024 * 1024,
    int(os.getenv("HALALSTREAM_MAX_REMOTE_DOWNLOAD_BYTES", str(500 * 1024 * 1024))),
)
MAX_LINK_DURATION_SECONDS = max(0, int(os.getenv("HALALSTREAM_MAX_LINK_DURATION_SECONDS", "600")))
COBALT_PARALLELISM = max(1, int(os.getenv("HALALSTREAM_COBALT_PARALLELISM", "1")))
COBALT_API_TIMEOUT = max(3, int(os.getenv("HALALSTREAM_COBALT_API_TIMEOUT", "8")))
COBALT_DOWNLOAD_TIMEOUT = max(10, int(os.getenv("HALALSTREAM_COBALT_DOWNLOAD_TIMEOUT", "25")))
VERIFY_PURIFIED_OUTPUT = os.getenv("HALALSTREAM_VERIFY_PURIFIED_OUTPUT", "1").strip().lower() in {"1", "true", "yes"}
RESIDUAL_MUSIC_RATIO_THRESHOLD = float(os.getenv("HALALSTREAM_RESIDUAL_MUSIC_RATIO_THRESHOLD", "0.12"))
RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD = float(os.getenv("HALALSTREAM_RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD", "0.01"))
VOICELESS_MUSIC_RATIO_THRESHOLD = float(os.getenv("HALALSTREAM_VOICELESS_MUSIC_RATIO_THRESHOLD", "0.92"))
STRICT_MUSIC_RATIO_THRESHOLD = float(os.getenv("HALALSTREAM_STRICT_MUSIC_RATIO_THRESHOLD", "0.45"))
STRICT_RESIDUAL_MUSIC_RATIO_THRESHOLD = float(os.getenv("HALALSTREAM_STRICT_RESIDUAL_MUSIC_RATIO_THRESHOLD", "0.06"))
STRICT_RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD = float(os.getenv("HALALSTREAM_STRICT_RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD", "0.004"))
UVR_RESCUE_ENABLED = os.getenv("HALALSTREAM_UVR_RESCUE_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
UVR_RESCUE_MODEL = os.getenv("HALALSTREAM_UVR_RESCUE_MODEL", "model_bs_roformer_ep_317_sdr_12.9755.ckpt")
UVR_RESCUE_MODELS = tuple(
    model.strip()
    for model in os.getenv(
        "HALALSTREAM_UVR_RESCUE_MODELS",
        UVR_RESCUE_MODEL,
    ).split(",")
    if model.strip()
)
UVR_MODEL_DIR = os.getenv("HALALSTREAM_UVR_MODEL_DIR", str(STORAGE / "audio-separator-models"))
VOICE_ENHANCE_ENABLED = os.getenv("HALALSTREAM_VOICE_ENHANCE_ENABLED", "0").strip().lower() not in {"0", "false", "no"}
VOICE_ENHANCE_FILTER = os.getenv(
    "HALALSTREAM_VOICE_ENHANCE_FILTER",
    "highpass=f=75,lowpass=f=11200,afftdn=nf=-26,"
    "equalizer=f=260:t=q:w=1.0:g=-2.2,"
    "equalizer=f=2400:t=q:w=1.0:g=2.0,"
    "equalizer=f=4200:t=q:w=1.0:g=2.3,"
    "equalizer=f=7200:t=q:w=1.1:g=1.4,"
    "acompressor=threshold=0.06:ratio=2.3:attack=8:release=180:makeup=4.5:knee=2.5,"
    "dynaudnorm=f=120:g=7:p=0.52:m=8,"
    "alimiter=limit=0.92",
).strip()
VOICE_ENHANCE_SPEECH_FILTER = os.getenv(
    "HALALSTREAM_VOICE_ENHANCE_SPEECH_FILTER",
    "highpass=f=105,lowpass=f=7800,afftdn=nf=-24,"
    "equalizer=f=260:t=q:w=1.0:g=-3.0,"
    "equalizer=f=1700:t=q:w=1.0:g=2.4,"
    "equalizer=f=3400:t=q:w=1.0:g=3.0,"
    "equalizer=f=6200:t=q:w=1.2:g=1.8,"
    "acompressor=threshold=0.05:ratio=2.8:attack=7:release=170:makeup=5.5:knee=2.5,"
    "dynaudnorm=f=120:g=7:p=0.56:m=9,"
    "alimiter=limit=0.90",
).strip()
MODAL_PURIFY_URL = os.getenv("HALALSTREAM_MODAL_PURIFY_URL", "").strip()
MODAL_PURIFY_SECRET = os.getenv("HALALSTREAM_MODAL_SECRET", "").strip()
MODAL_PURIFY_TIMEOUT = max(300, int(os.getenv("HALALSTREAM_MODAL_PURIFY_TIMEOUT", "1800")))
TUNELIO_API_KEY = os.getenv("HALALSTREAM_TUNELIO_API_KEY", "").strip()
TUNELIO_API_URL = os.getenv("HALALSTREAM_TUNELIO_API_URL", "https://tunelio.dev").rstrip("/")
TUNELIO_TIMEOUT = max(30, int(os.getenv("HALALSTREAM_TUNELIO_TIMEOUT", "180")))
ENABLE_TUNELIO_FALLBACK = os.getenv("HALALSTREAM_ENABLE_TUNELIO_FALLBACK", "0").strip().lower() in {
    "1",
    "true",
    "yes",
}
NOADSDL_ENABLED = os.getenv("HALALSTREAM_NOADSDL_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
NOADSDL_API_URL = os.getenv("HALALSTREAM_NOADSDL_API_URL", "https://noadsdl.com").rstrip("/")
NOADSDL_API_TIMEOUT = max(10, int(os.getenv("HALALSTREAM_NOADSDL_API_TIMEOUT", "40")))
NOADSDL_JOB_TIMEOUT = max(30, int(os.getenv("HALALSTREAM_NOADSDL_JOB_TIMEOUT", "180")))
NOADSDL_DOWNLOAD_TIMEOUT = max(30, int(os.getenv("HALALSTREAM_NOADSDL_DOWNLOAD_TIMEOUT", "120")))
HOSTED_SPACE = bool(os.getenv("SPACE_ID") or os.getenv("SPACE_HOST"))
ALLOW_LINK_DOWNLOADS = os.getenv("HALALSTREAM_ALLOW_LINK_DOWNLOADS", "").strip().lower() in {"1", "true", "yes"}
LINK_DOWNLOADS_RELIABLE = True
YOUTUBE_CLIENT_FALLBACKS = tuple(
    () if client.strip().lower() in {"default", "auto"} else (client.strip(),)
    for client in os.getenv("HALALSTREAM_YOUTUBE_CLIENTS", "web,mweb").split(",")
    if client.strip()
) or ((),)
YOUTUBE_SOCKET_TIMEOUT = int(os.getenv("HALALSTREAM_YOUTUBE_SOCKET_TIMEOUT", "12"))
YOUTUBE_RETRIES = int(os.getenv("HALALSTREAM_YOUTUBE_RETRIES", "1"))
YTDLP_COOKIES = os.getenv("HALALSTREAM_YTDLP_COOKIES", "").strip()
YOUTUBE_POT_BASE_URL = os.getenv("HALALSTREAM_YOUTUBE_POT_BASE_URL", "").strip()
YOUTUBE_FETCH_POT = os.getenv("HALALSTREAM_YOUTUBE_FETCH_POT", "auto").strip().lower()
if YOUTUBE_FETCH_POT not in {"auto", "always", "never"}:
    YOUTUBE_FETCH_POT = "auto"
YOUTUBE_REMOTE_COMPONENTS = tuple(
    component.strip()
    for component in os.getenv("HALALSTREAM_YOUTUBE_REMOTE_COMPONENTS", "ejs:github").split(",")
    if component.strip()
)
YTDLP_PROXY = os.getenv("HALALSTREAM_YTDLP_PROXY", "").strip()

COBALT_FALLBACK_APIS = tuple(
    api.strip().rstrip("/") + "/"
    for api in os.getenv(
        "HALALSTREAM_COBALT_APIS",
        "https://rue-cobalt.xenon.zone/,https://cobaltapi.kittycat.boo/",
    ).split(",")
    if api.strip()
)
COBALT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ASSETS_DIR.mkdir(exist_ok=True)
STORAGE.mkdir(exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)


class LinkDurationLimitError(RuntimeError):
    pass


app = FastAPI(title="HalalStream Server", version="0.3.0")
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
    return response

jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()
processing_semaphore = threading.BoundedSemaphore(MAX_ACTIVE_PROCESSING_JOBS)
queue_lock = threading.Lock()
waiting_processing_jobs: list[str] = []
active_processing_jobs: set[str] = set()


class LinkJobRequest(BaseModel):
    url: HttpUrl
    purify_mode: str = "purify"
    quality: str = "high"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/app.js")
def app_js() -> FileResponse:
    return FileResponse(
        ROOT / "app.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/styles.css")
def styles() -> FileResponse:
    return FileResponse(
        ROOT / "styles.css",
        media_type="text/css",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/health")
def health() -> Dict[str, Any]:
    with queue_lock:
        waiting_count = len(waiting_processing_jobs)
        active_count = len(active_processing_jobs)
    return {
        "ok": True,
        "python": sys.version.split()[0],
        "ffmpeg": bool(get_ffmpeg_path()),
        "yt_dlp": yt_dlp is not None,
        "demucs": has_module("demucs"),
        "demucs_model": DEMUCS_MODEL,
        "demucs_jobs": DEMUCS_JOBS,
        "demucs_shifts": DEMUCS_SHIFTS,
        "demucs_segment": DEMUCS_SEGMENT,
        "demucs_max_segment": DEMUCS_MAX_SEGMENT,
        "demucs_overlap": DEMUCS_OVERLAP,
        "music_ratio_threshold": MUSIC_RATIO_THRESHOLD,
        "strict_direct_bypass": not ALLOW_UNCHECKED_DIRECT,
        "auto_purify_on_detection": AUTO_PURIFY_ON_DETECTION,
        "active_processing_jobs": active_count,
        "waiting_processing_jobs": waiting_count,
        "estimated_processing_seconds": ESTIMATED_PROCESSING_SECONDS,
        "job_ttl_hours": JOB_TTL_HOURS,
        "max_upload_mb": round(MAX_UPLOAD_BYTES / (1024 * 1024), 1),
        "max_remote_download_mb": round(MAX_REMOTE_DOWNLOAD_BYTES / (1024 * 1024), 1),
        "max_link_duration_seconds": MAX_LINK_DURATION_SECONDS,
        "cobalt_parallelism": COBALT_PARALLELISM,
        "verify_purified_output": VERIFY_PURIFIED_OUTPUT,
        "residual_music_ratio_threshold": RESIDUAL_MUSIC_RATIO_THRESHOLD,
        "voiceless_music_ratio_threshold": VOICELESS_MUSIC_RATIO_THRESHOLD,
        "strict_music_ratio_threshold": STRICT_MUSIC_RATIO_THRESHOLD,
        "strict_residual_music_ratio_threshold": STRICT_RESIDUAL_MUSIC_RATIO_THRESHOLD,
        "strict_residual_music_absolute_threshold": STRICT_RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD,
        "voice_enhance_enabled": VOICE_ENHANCE_ENABLED,
        "voice_restore_profile": "raw_roformer_v1",
        "modal_purify_enabled": bool(MODAL_PURIFY_URL and MODAL_PURIFY_SECRET and requests is not None),
        "modal_purify_url_configured": bool(MODAL_PURIFY_URL),
        "noadsdl_download_enabled": bool(NOADSDL_ENABLED and requests is not None),
        "tunelio_download_enabled": bool(
            ENABLE_TUNELIO_FALLBACK and TUNELIO_API_KEY and requests is not None
        ),
        "max_active_processing_jobs": MAX_ACTIVE_PROCESSING_JOBS,
        "cuda_available": (lambda: __import__("torch").cuda.is_available() if has_module("torch") else False)(),
        "hosted_space": HOSTED_SPACE,
        "link_downloads_reliable": LINK_DOWNLOADS_RELIABLE,
        "yt_dlp_proxy": bool(YTDLP_PROXY),
        "youtube_pot_provider": bool(YOUTUBE_POT_BASE_URL),
        "youtube_fetch_pot": YOUTUBE_FETCH_POT,
        "youtube_remote_components": list(YOUTUBE_REMOTE_COMPONENTS),
        "message": "الخادم يعمل. اكتمال المعالجة يحتاج yt-dlp و ffmpeg، ومعهما إما demucs محلي أو عامل Modal.",
    }


@app.post("/api/jobs/link")
def create_link_job(payload: LinkJobRequest) -> Dict[str, str]:
    if is_youtube_url(str(payload.url)) and not LINK_DOWNLOADS_RELIABLE:
        raise HTTPException(
            status_code=400,
            detail="الاستضافة الحالية لا تنزّل روابط YouTube بثبات. نزّل الملف على جهازك ثم ارفعه من تبويب ملف.",
        )
    job = create_job(
        "link",
        source_url=str(payload.url),
        purify_mode=payload.purify_mode,
        quality=payload.quality
    )
    start_worker(process_job, job["id"])
    return {"id": job["id"]}


@app.post("/api/jobs/upload")
async def create_upload_job(
    file: UploadFile = File(...),
    purify_mode: str = Form("purify"),
    quality: str = Form("high")
) -> Dict[str, str]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="لم يصل اسم الملف إلى الخادم.")

    job = create_job(
        "upload",
        source_name=file.filename,
        purify_mode=purify_mode,
        quality=quality
    )
    workdir = job_dir(job["id"])
    suffix = safe_suffix(file.filename)
    original_path = workdir / f"uploaded{suffix}"

    written = 0
    exceeded_limit = False
    with original_path.open("wb") as target:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                exceeded_limit = True
                break
            target.write(chunk)

    if exceeded_limit:
        original_path.unlink(missing_ok=True)
        update_job(
            job["id"],
            status="failed",
            stage="حجم الملف كبير",
            progress=0,
            message=f"حجم الملف أكبر من الحد المسموح حالياً ({MAX_UPLOAD_BYTES // (1024 * 1024)} MB).",
        )
        raise HTTPException(
            status_code=413,
            detail=f"حجم الملف أكبر من الحد المسموح حالياً ({MAX_UPLOAD_BYTES // (1024 * 1024)} MB).",
        )

    update_job(job["id"], original_path=str(original_path), title=file.filename)
    start_worker(process_job, job["id"])
    return {"id": job["id"]}


@app.get("/api/jobs/latest")
def latest_job() -> Dict[str, Any]:
    with jobs_lock:
        latest = max(jobs.values(), key=lambda item: item.get("updated_at", 0), default=None)
    if not latest:
        raise HTTPException(status_code=404, detail="لا توجد مهام محفوظة بعد.")
    job = public_job(latest["id"])
    if not job:
        raise HTTPException(status_code=404, detail="لا توجد مهام محفوظة بعد.")
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = public_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="لم يتم العثور على المهمة.")
    return job


@app.post("/api/jobs/{job_id}/purify")
def purify(job_id: str) -> Dict[str, str]:
    job = get_internal_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="لم يتم العثور على المهمة.")
    if job["status"] not in {"needs_consent", "failed_after_detection"}:
        raise HTTPException(status_code=409, detail="لا يمكن بدء إزالة المعازف في الحالة الحالية.")

    update_job(job_id, status="purifying", stage="إزالة المعازف", progress=82, message="نبدأ إزالة المعازف ونجهز نسخة منقّاة الآن.")
    start_worker(purify_job, job_id)
    return {"id": job_id}


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: str) -> Dict[str, str]:
    job = get_internal_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="لم يتم العثور على المهمة.")
    if job["status"] != "failed":
        raise HTTPException(status_code=409, detail="إعادة المحاولة متاحة فقط بعد فشل المعالجة.")

    clear_generated_outputs(job_id)
    update_job(
        job_id,
        status="queued",
        stage="إعادة المحاولة",
        progress=3,
        message="نعيد المعالجة من الملف الموجود لتوفير وقت التحميل.",
        has_music=None,
        instrumental_ratio=None,
        confidence=None,
        residual_music_ratio=None,
        audio_path=None,
        vocals_path=None,
        instrumental_path=None,
        purified_path=None,
        purified_audio_path=None,
        clean_audio_path=None,
        error=None,
    )
    start_worker(process_job, job_id)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str, kind: str = "purified") -> FileResponse:
    job = get_internal_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="لم يتم العثور على المهمة.")

    if kind == "original":
        if job["status"] not in {"clean", "direct"}:
            raise HTTPException(status_code=403, detail="لا يسمح بتحميل الملف الأصلي إلا بعد ثبوت خلوه من المعازف أو اختيار التحميل المباشر.")
        path = job.get("original_path")
        prefix = "halalstream-direct" if job["status"] == "direct" else "halalstream-clean"
        filename = f"{prefix}-{job_id}{Path(path).suffix if path else '.mp4'}"
    elif kind == "clean_audio":
        if job["status"] != "clean":
            raise HTTPException(status_code=403, detail="الصوت النظيف غير متاح إلا بعد ثبوت خلو المقطع من المعازف.")
        path = job.get("clean_audio_path")
        filename = f"halalstream-clean-audio-{job_id}{Path(path).suffix if path else '.m4a'}"
    elif kind == "purified_audio":
        if job["status"] != "complete":
            raise HTTPException(status_code=403, detail="الصوت المنقّى غير جاهز بعد.")
        path = job.get("purified_audio_path")
        filename = f"halalstream-purified-audio-{job_id}{Path(path).suffix if path else '.m4a'}"
    elif kind == "log":
        path = job_dir(job_id) / "cmd_log.txt"
        filename = f"cmd_log-{job_id}.txt"
    else:
        if job["status"] != "complete":
            raise HTTPException(status_code=403, detail="الملف المنقّى غير جاهز بعد.")
        path = job.get("purified_path")
        filename = f"halalstream-purified-{job_id}{Path(path).suffix if path else '.mp4'}"

    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="ملف التحميل غير موجود على القرص.")
    return FileResponse(path, filename=filename)


def create_job(
    source_type: str,
    source_url: Optional[str] = None,
    source_name: Optional[str] = None,
    purify_mode: str = "purify",
    quality: str = "high"
) -> Dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    workdir = job_dir(job_id)
    workdir.mkdir(parents=True, exist_ok=True)
    job = {
        "id": job_id,
        "status": "queued",
        "stage": "في قائمة الانتظار",
        "message": "استلمنا الطلب، وسيبدأ خادم المعالجة الآن.",
        "progress": 2,
        "source_type": source_type,
        "source_url": source_url,
        "source_name": source_name,
        "purify_mode": purify_mode,
        "quality": quality,
        "title": source_name or "مقطع من رابط",
        "created_at": time.time(),
        "updated_at": time.time(),
        "has_music": None,
        "instrumental_ratio": None,
        "confidence": None,
        "residual_music_ratio": None,
        "purification_mode": None,
        "original_path": None,
        "audio_path": None,
        "vocals_path": None,
        "instrumental_path": None,
        "purified_path": None,
        "purified_audio_path": None,
        "clean_audio_path": None,
        "duration_seconds": None,
        "queue_position": 0,
        "queue_length": 0,
        "estimated_wait_seconds": 0,
        "error": None,
    }
    with jobs_lock:
        jobs[job_id] = job
    persist_job(job_id)
    return job


def process_job(job_id: str) -> None:
    release_slot = None
    try:
        job = get_internal_job(job_id)
        if not job:
            return
        purify_mode = job.get("purify_mode", "purify")

        if purify_mode == "direct":
            if not ALLOW_UNCHECKED_DIRECT:
                raise RuntimeError("التحميل المباشر غير مفعّل حالياً على الخادم.")
            original = prepare_original(job_id, job, for_direct=True)
            complete_direct_job(job_id, original)
            return

        original = prepare_original(job_id, job)

        if modal_purify_available():
            process_job_with_modal(job_id, original)
            return

        ensure_runtime()
        release_slot = acquire_processing_slot(job_id)
        update_job(job_id, queue_position=0, queue_length=0, estimated_wait_seconds=0)

        quality = job.get("quality", "high")

        audio = extract_audio(job_id, original)
        vocals, instrumental = separate_vocals(job_id, audio, quality=quality)
        ratio, confidence = estimate_music_ratio(audio, vocals, instrumental)
        has_music = ratio >= MUSIC_RATIO_THRESHOLD

        if has_music:
            update_job(
                job_id,
                status="purifying" if AUTO_PURIFY_ON_DETECTION else "needs_consent",
                stage="إزالة المعازف" if AUTO_PURIFY_ON_DETECTION else "الحكم الشرعي قبل التحميل",
                progress=80 if AUTO_PURIFY_ON_DETECTION else 78,
                message=(
                    "تم رصد مسار معازف. نبدأ إزالة المعازف تلقائياً ولا نطلق الملف الأصلي."
                    if AUTO_PURIFY_ON_DETECTION
                    else "تم رصد مسار معازف. أوقفنا التحميل حتى تختار إزالة المعازف."
                ),
                has_music=True,
                instrumental_ratio=round(ratio, 4),
                confidence=round(confidence, 4),
                vocals_path=str(vocals),
                instrumental_path=str(instrumental),
            )
            if AUTO_PURIFY_ON_DETECTION:
                purify_job(job_id)
            return

        clean_audio = encode_audio(job_id, audio, "clean-audio.m4a", 92, "نجهز نسخة صوتية نظيفة للتحميل.")
        update_job(
            job_id,
            status="clean",
            stage="جاهز للتحميل",
            progress=100,
            message="الحمد لله، لم يظهر مؤشر معتبر للمعازف. يمكنك تحميل المقطع أو الصوت فقط.",
            has_music=False,
            instrumental_ratio=round(ratio, 4),
            confidence=round(confidence, 4),
            vocals_path=str(vocals),
            instrumental_path=str(instrumental),
            clean_audio_path=str(clean_audio),
        )
    except Exception as exc:
        fail_job(job_id, exc)
    finally:
        if release_slot:
            release_slot()


def prepare_original(job_id: str, job: Dict[str, Any], for_direct: bool = False) -> Path:
    if job["source_type"] == "link":
        existing_original = job.get("original_path")
        if existing_original and Path(existing_original).exists():
            original = Path(existing_original)
            update_job(
                job_id,
                status="downloading" if for_direct else "extracting",
                stage="تجهيز التحميل" if for_direct else "استخراج الصوت",
                progress=80 if for_direct else 24,
                message="نستخدم الملف الموجود ونجهزه للتحميل المباشر." if for_direct else "نستخدم الملف الموجود ونستخرج الصوت من جديد.",
            )
        else:
            original = download_link(job_id, job["source_url"])
        enforce_link_file_duration(job_id, original)
    else:
        original = Path(job["original_path"])
        update_job(
            job_id,
            status="downloading" if for_direct else "extracting",
            stage="تجهيز التحميل" if for_direct else "استخراج الصوت",
            progress=80 if for_direct else 24,
            message="نجهز الملف للتحميل المباشر دون فحص." if for_direct else "نجهز الملف للتحليل.",
        )
    return original


def complete_direct_job(job_id: str, original: Path) -> None:
    update_job(
        job_id,
        status="direct",
        stage="جاهز للتحميل",
        progress=100,
        message="تم تجهيز الملف للتحميل المباشر كما هو، دون فحص أو تنقية.",
        has_music=None,
        instrumental_ratio=None,
        confidence=None,
        residual_music_ratio=None,
        purification_mode=None,
        original_path=str(original),
        audio_path=None,
        vocals_path=None,
        instrumental_path=None,
        clean_audio_path=None,
        purified_path=None,
        purified_audio_path=None,
        queue_position=0,
        queue_length=0,
        estimated_wait_seconds=0,
    )


def modal_purify_available() -> bool:
    return bool(MODAL_PURIFY_URL and MODAL_PURIFY_SECRET and requests is not None)


def process_job_with_modal(job_id: str, original: Path) -> None:
    if requests is None:
        raise RuntimeError("مكتبة requests غير مثبتة، ولا يمكن الاتصال بعامل Modal.")

    modal_audio = prepare_modal_audio(job_id, original)
    update_job(
        job_id,
        status="separating",
        stage="تنقية خارجية",
        progress=38,
        message="نرسل الملف إلى عامل Modal لتشغيل التنقية على GPU عند الطلب.",
        queue_position=0,
        queue_length=0,
        estimated_wait_seconds=0,
    )
    last_error = None
    for attempt in range(1, 4):
        try:
            update_job(
                job_id,
                message="نرسل الملف إلى عامل Modal لتشغيل التنقية على GPU عند الطلب."
                if attempt == 1
                else f"نعيد الاتصال بعامل Modal، المحاولة {attempt} من 3.",
            )
            with modal_audio.open("rb") as file_obj:
                response = requests.post(
                    MODAL_PURIFY_URL,
                    headers={"x-halalstream-secret": MODAL_PURIFY_SECRET},
                    data={
                        "filename": modal_audio.name,
                        "original_filename": original.name,
                        "quality": "high",
                    },
                    files={"file": (modal_audio.name, file_obj, "audio/flac")},
                    timeout=MODAL_PURIFY_TIMEOUT,
                )
            if response.status_code != 200:
                raise RuntimeError(f"فشل عامل Modal: HTTP {response.status_code}\n{response.text[:1200]}")
            payload = response.json()
            break
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 * attempt)
    else:
        raise RuntimeError(f"تعذر تشغيل التنقية عبر Modal.\n{last_error}") from last_error

    audio_b64 = payload.get("audio_base64")
    if not audio_b64:
        raise RuntimeError("عامل Modal لم يرجع ملف صوت صالح.")

    audio_bytes = base64.b64decode(audio_b64)
    status = payload.get("status") or "complete"
    ratio = payload.get("instrumental_ratio")
    residual = payload.get("residual_music_ratio")
    mode = payload.get("purification_mode")
    audio_name = "clean-audio.m4a" if status == "clean" else "purified-audio.m4a"
    audio_path = job_dir(job_id) / audio_name
    audio_path.write_bytes(audio_bytes)

    common = {
        "has_music": payload.get("has_music"),
        "instrumental_ratio": round(float(ratio), 4) if ratio is not None else None,
        "confidence": payload.get("confidence"),
        "residual_music_ratio": round(float(residual), 4) if residual is not None else None,
        "purification_mode": mode,
        "audio_path": str(audio_path),
    }

    if status == "clean":
        update_job(
            job_id,
            status="clean",
            stage="جاهز للتحميل",
            progress=100,
            message=payload.get("message") or "الحمد لله، لم يظهر مؤشر معتبر للمعازف. يمكنك تحميل المقطع أو الصوت فقط.",
            clean_audio_path=str(audio_path),
            **common,
        )
        return

    out = mux_audio_to_video(job_id, original, audio_path)
    update_job(
        job_id,
        status="complete",
        stage="تمت إزالة المعازف",
        progress=100,
        message=payload.get("message") or "تم بحمد الله إعداد نسخة منقّاة عبر عامل Modal. الملف جاهز للتحميل.",
        purified_path=str(out),
        purified_audio_path=str(audio_path),
        **common,
    )


def prepare_modal_audio(job_id: str, original: Path) -> Path:
    ffmpeg = require_ffmpeg()
    out = job_dir(job_id) / "modal-input.flac"
    update_job(
        job_id,
        status="extracting",
        stage="تجهيز الصوت",
        progress=30,
        message="نجهز مسار صوت نقي لإرساله إلى عامل Modal بدل رفع الفيديو كاملاً.",
    )
    run_cmd(
        [
            ffmpeg,
            "-y",
            "-i",
            str(original),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-sample_fmt",
            "s16",
            "-c:a",
            "flac",
            str(out),
        ],
        "فشل تجهيز الصوت لإرساله إلى عامل Modal.",
        job_id=job_id,
    )
    return out


def mux_audio_to_video(job_id: str, original: Path, audio_out: Path) -> Path:
    ffmpeg = require_ffmpeg()
    out = job_dir(job_id) / "purified.mp4"
    update_job(job_id, progress=92, message="نركّب الصوت المنقّى على المقطع دون إعادة ضغط الفيديو.")
    run_cmd(
        [
            ffmpeg,
            "-y",
            "-i",
            str(original),
            "-i",
            str(audio_out),
            "-map",
            "0:v:0?",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out),
        ],
        "فشل تركيب الصوت المنقّى على الفيديو.",
        job_id=job_id,
    )
    return out


def purify_job(job_id: str) -> None:
    try:
        job = get_internal_job(job_id)
        if not job:
            return
        original = Path(job["original_path"])
        if not job.get("vocals_path"):
            raise RuntimeError("لم نجد مسار الصوت البشري الناتج من نموذج العزل.")
        vocals = Path(job["vocals_path"])
        if not vocals.exists():
            raise RuntimeError("لم نجد مسار الصوت البشري الناتج من نموذج العزل.")

        instrumental = Path(job["instrumental_path"]) if job.get("instrumental_path") else None
        purification_result = None
        if instrumental and instrumental.exists():
            source_ratio = float(job.get("instrumental_ratio") or 0.0)
            if is_voiceless_music(source_ratio):
                update_job(
                    job_id,
                    progress=84,
                    message="المقطع يغلب عليه العزف ولا يظهر صوت بشري موثوق. نخرج مساراً صامتاً بدل تمرير المعازف.",
                    purification_mode="silence_no_voice",
                )
                reference_audio = Path(job["audio_path"]) if job.get("audio_path") else vocals
                purification_result = silence_result_for_voiceless_music(job_id, reference_audio)
            else:
                update_job(job_id, progress=84, message="نزيل بقايا المعازف من مسار الكلام مع الحفاظ على وضوح الصوت.")
                source_audio = Path(job["audio_path"]) if job.get("audio_path") else vocals
                purification_result = purify_with_retries(job_id, source_audio, vocals, instrumental, source_ratio=source_ratio)
            purified_vocals = purification_result["path"]
        else:
            purified_vocals = vocals

        audio_out = encode_audio(
            job_id,
            purified_vocals,
            "purified-audio.m4a",
            86,
            "نجهز مسار الصوت المعزول كما أنتجه النموذج بلا فلاتر تغيّر طبيعته.",
            filter_vocals=False,
        )
        out = mux_audio_to_video(job_id, original, audio_out)
        update_job(
            job_id,
            status="complete",
            stage="تمت إزالة المعازف",
            progress=100,
            message=completion_message(purification_result),
            purified_path=str(out),
            purified_audio_path=str(audio_out),
        )
    except Exception as exc:
        fail_job(job_id, exc)


def download_via_cobalt(job_id: str, url: str, workdir: Path) -> Path:
    errors: list[str] = []
    indexed_apis = list(enumerate(COBALT_FALLBACK_APIS))
    for start in range(0, len(indexed_apis), COBALT_PARALLELISM):
        batch = indexed_apis[start:start + COBALT_PARALLELISM]
        labels = "، ".join(api.split("//", 1)[1].split("/", 1)[0] for _, api in batch)
        update_job(job_id, message=f"نبحث عن أسرع خادم تنزيل متاح: {labels}")
        executor = ThreadPoolExecutor(max_workers=len(batch))
        futures = {
            executor.submit(try_cobalt_download, job_id, url, workdir, api_url, index): api_url
            for index, api_url in batch
        }
        try:
            for future in as_completed(futures):
                api_url = futures[future]
                try:
                    path = future.result()
                    if path and path.exists() and path.stat().st_size > 0:
                        for other in futures:
                            if other is not future:
                                other.cancel()
                        update_job(job_id, message=f"اكتمل التحميل عبر {api_url.split('//', 1)[1].split('/', 1)[0]}.")
                        executor.shutdown(wait=False, cancel_futures=True)
                        return path
                except Exception as exc:
                    errors.append(f"{api_url}: {exc}")
                    print(f"Cobalt download failed on {api_url}: {exc}")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    if errors:
        print("Cobalt errors:", "\n".join(errors[-5:]))
    raise RuntimeError("فشلت جميع محاولات التنزيل المباشرة وعبر الخوادم المساندة.")


def download_via_noadsdl(job_id: str, url: str, workdir: Path) -> tuple[Path, str]:
    if requests is None or not NOADSDL_ENABLED:
        raise RuntimeError("خادم التنزيل المجاني غير مفعّل.")

    api_host = urlparse(NOADSDL_API_URL).netloc.lower()

    def trusted_api_url(path: str) -> str:
        target = urljoin(f"{NOADSDL_API_URL}/", str(path))
        if urlparse(target).netloc.lower() != api_host:
            raise RuntimeError("أعاد خادم التنزيل رابطاً خارجياً غير موثوق.")
        return target

    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": COBALT_USER_AGENT,
        }
    )

    update_job(job_id, message="نطلب المقطع من خادم التنزيل المجاني.")
    info_response = session.get(
        f"{NOADSDL_API_URL}/api/video-info",
        params={"url": url},
        timeout=(10, NOADSDL_API_TIMEOUT),
    )
    info_response.raise_for_status()
    info = info_response.json()
    if not info.get("success"):
        raise RuntimeError(str(info.get("error") or "تعذر استخراج بيانات المقطع."))
    duration_seconds = coerce_duration_seconds(info.get("duration"))
    if duration_seconds is not None:
        update_job(job_id, duration_seconds=round(duration_seconds, 2))
    enforce_link_duration_seconds(duration_seconds, "الرابط")

    selected_format_id = "720"
    selected_height = -1
    for name, format_info in (info.get("video_formats") or {}).items():
        if not isinstance(format_info, dict):
            continue
        resolution = str(format_info.get("resolution") or "")
        try:
            height = int(resolution.rsplit("x", 1)[-1])
        except ValueError:
            height = 720 if "720" in str(name) else 0
        if 0 < height <= 720 and height > selected_height:
            selected_height = height
            selected_format_id = str(format_info.get("format_id") or height)

    start_response = session.get(
        f"{NOADSDL_API_URL}/download",
        params={
            "url": url,
            "format": "mp4",
            "format_id": selected_format_id,
            "async": "1",
        },
        timeout=(10, NOADSDL_API_TIMEOUT),
    )
    start_response.raise_for_status()
    started = start_response.json()
    status_url = started.get("status_url")
    direct_url = started.get("direct_url") or started.get("download_url")
    if not status_url and not direct_url:
        raise RuntimeError(str(started.get("error") or "لم يبدأ خادم التنزيل المهمة."))

    deadline = time.monotonic() + NOADSDL_JOB_TIMEOUT
    last_message_at = 0.0
    while not direct_url:
        if time.monotonic() >= deadline:
            raise RuntimeError("انتهت مهلة انتظار خادم التنزيل المجاني.")
        status_response = session.get(
            trusted_api_url(str(status_url)),
            timeout=(10, NOADSDL_API_TIMEOUT),
        )
        status_response.raise_for_status()
        status_payload = status_response.json()
        status = str(status_payload.get("status") or "").lower()
        direct_url = status_payload.get("direct_url") or status_payload.get("download_url")
        if status in {"failed", "error"}:
            raise RuntimeError(str(status_payload.get("message") or "فشل تجهيز المقطع."))
        now = time.monotonic()
        if now - last_message_at >= 5:
            update_job(job_id, message="خادم التنزيل المجاني يجهز ملف MP4.")
            last_message_at = now
        if not direct_url:
            time.sleep(2)

    update_job(job_id, message="اكتمل التجهيز المجاني. ننقل الملف إلى خادم المعالجة.")
    partial_path = workdir / "downloaded-noads.part"
    out_path = workdir / "downloaded-noads.mp4"
    with session.get(
        trusted_api_url(str(direct_url)),
        stream=True,
        timeout=(15, NOADSDL_DOWNLOAD_TIMEOUT),
    ) as download:
        download.raise_for_status()
        try:
            content_length = int(download.headers.get("Content-Length") or "0")
        except ValueError:
            content_length = 0
        if content_length > MAX_REMOTE_DOWNLOAD_BYTES:
            raise RuntimeError(
                f"حجم الملف من الرابط أكبر من الحد المسموح ({MAX_REMOTE_DOWNLOAD_BYTES // (1024 * 1024)} MB)."
            )

        downloaded = 0
        with partial_path.open("wb") as target:
            for chunk in download.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > MAX_REMOTE_DOWNLOAD_BYTES:
                    partial_path.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"حجم الملف من الرابط أكبر من الحد المسموح ({MAX_REMOTE_DOWNLOAD_BYTES // (1024 * 1024)} MB)."
                    )
                target.write(chunk)

    if not partial_path.exists() or partial_path.stat().st_size <= 0:
        partial_path.unlink(missing_ok=True)
        raise RuntimeError("أعاد خادم التنزيل المجاني ملفاً فارغاً.")
    partial_path.replace(out_path)
    return out_path, str(info.get("title") or "مقطع من YouTube")


def download_via_tunelio(job_id: str, url: str, workdir: Path) -> tuple[Path, str]:
    if requests is None or not TUNELIO_API_KEY:
        raise RuntimeError("خدمة تنزيل YouTube الأساسية غير مفعّلة.")

    update_job(job_id, message="نجهز رابط YouTube عبر خادم التنزيل السريع.")
    response = requests.get(
        f"{TUNELIO_API_URL}/create",
        params={"quality": "720p", "url": url},
        headers={"Authorization": f"Bearer {TUNELIO_API_KEY}"},
        timeout=60,
    )
    if response.status_code != 200:
        try:
            detail = response.json().get("message") or response.json().get("error")
        except Exception:
            detail = response.text[:300]
        raise RuntimeError(f"فشلت خدمة تنزيل YouTube: HTTP {response.status_code} {detail or ''}".strip())

    payload = response.json()
    download_url = str(payload.get("url") or "")
    if not download_url.startswith("https://"):
        raise RuntimeError("خدمة تنزيل YouTube لم تعد رابطاً آمناً.")
    expected_size = int(payload.get("file_size") or 0)
    if expected_size > MAX_REMOTE_DOWNLOAD_BYTES:
        raise RuntimeError(
            f"حجم الملف من الرابط أكبر من الحد المسموح ({MAX_REMOTE_DOWNLOAD_BYTES // (1024 * 1024)} MB)."
        )

    filename = str(payload.get("filename") or "downloaded.mp4")
    suffix = safe_suffix(filename)
    if suffix == ".bin":
        suffix = ".mp4"
    partial_path = workdir / "downloaded-tunelio.part"
    out_path = workdir / f"downloaded-tunelio{suffix}"
    downloaded = 0
    try:
        with requests.get(download_url, stream=True, timeout=TUNELIO_TIMEOUT) as download:
            download.raise_for_status()
            try:
                content_length = int(download.headers.get("Content-Length") or "0")
            except ValueError:
                content_length = 0
            if content_length > MAX_REMOTE_DOWNLOAD_BYTES:
                raise RuntimeError(
                    f"حجم الملف من الرابط أكبر من الحد المسموح ({MAX_REMOTE_DOWNLOAD_BYTES // (1024 * 1024)} MB)."
                )
            with partial_path.open("wb") as target:
                for chunk in download.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_REMOTE_DOWNLOAD_BYTES:
                        raise RuntimeError(
                            f"حجم الملف من الرابط أكبر من الحد المسموح ({MAX_REMOTE_DOWNLOAD_BYTES // (1024 * 1024)} MB)."
                        )
                    target.write(chunk)
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise

    if downloaded <= 0:
        partial_path.unlink(missing_ok=True)
        raise RuntimeError("خدمة تنزيل YouTube أعادت ملفاً فارغاً.")
    partial_path.replace(out_path)
    return out_path, str(payload.get("title") or filename or "مقطع من YouTube")


def try_cobalt_download(job_id: str, url: str, workdir: Path, api_url: str, index: int) -> Optional[Path]:
    context = ssl._create_unverified_context()
    payload = {
        "url": url,
        "downloadMode": "auto",
        "videoQuality": "720",
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": COBALT_USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, context=context, timeout=COBALT_API_TIMEOUT) as response:
        res = json.loads(response.read().decode("utf-8"))

    status = res.get("status")
    download_url = None
    filename = None
    if status in ("tunnel", "redirect"):
        download_url = res.get("url")
        filename = res.get("filename")
    elif status == "picker":
        picker_items = res.get("picker", [])
        if picker_items:
            download_url = picker_items[0].get("url")
            filename = picker_items[0].get("filename")

    if not download_url:
        return None

    suffix = safe_suffix(filename or "downloaded.mp4")
    partial_path = workdir / f"downloaded-{index}.part"
    out_path = workdir / f"downloaded-{index}{suffix}"
    dl_req = urllib.request.Request(download_url, headers={"User-Agent": COBALT_USER_AGENT})
    with urllib.request.urlopen(dl_req, context=context, timeout=COBALT_DOWNLOAD_TIMEOUT) as dl_res:
        try:
            content_length = int(dl_res.headers.get("Content-Length") or "0")
        except ValueError:
            content_length = 0
        if content_length > MAX_REMOTE_DOWNLOAD_BYTES:
            raise RuntimeError(
                f"حجم الملف من الرابط أكبر من الحد المسموح ({MAX_REMOTE_DOWNLOAD_BYTES // (1024 * 1024)} MB)."
            )
        downloaded = 0
        with partial_path.open("wb") as f:
            while True:
                chunk = dl_res.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_REMOTE_DOWNLOAD_BYTES:
                    partial_path.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"حجم الملف من الرابط أكبر من الحد المسموح ({MAX_REMOTE_DOWNLOAD_BYTES // (1024 * 1024)} MB)."
                    )
                f.write(chunk)
    if partial_path.exists() and partial_path.stat().st_size > 0:
        partial_path.replace(out_path)
        return out_path
    return None


def download_link(job_id: str, url: str) -> Path:
    # Clean tracking query parameters for safer downloading, especially for Instagram/TikTok/Shorts
    if "instagram.com" in url.lower() or "tiktok.com" in url.lower() or "/shorts/" in url.lower() or "youtu.be" in url.lower():
        if "?" in url:
            url = url.split("?")[0]
            
    workdir = job_dir(job_id)
    update_job(job_id, status="downloading", stage="تحميل المقطع", progress=8, message="نحمّل المقطع إلى خادم المعالجة.")
    download_errors: list[str] = []
    info: Optional[Dict[str, Any]] = None

    is_yt = is_youtube_url(url)

    if is_yt:
        try:
            media_path, title = download_via_noadsdl(job_id, url, workdir)
            update_job(
                job_id,
                original_path=str(media_path),
                title=title,
                status="extracting",
                stage="استخراج الصوت",
                progress=24,
                message="اكتمل التحميل المجاني. نستخرج المسار الصوتي الآن.",
            )
            return media_path
        except LinkDurationLimitError:
            raise
        except Exception as exc:
            download_errors.append(f"NoAdsDL: {exc}")
            update_job(job_id, message="تعذر الخادم المجاني الأساسي. نجرب خادماً مجتمعياً.")

        try:
            media_path = download_via_cobalt(job_id, url, workdir)
            update_job(
                job_id,
                original_path=str(media_path),
                title=media_path.name,
                status="extracting",
                stage="استخراج الصوت",
                progress=24,
                message="اكتمل التحميل عبر خادم مجاني. نستخرج المسار الصوتي الآن.",
            )
            return media_path
        except LinkDurationLimitError:
            raise
        except Exception as exc:
            download_errors.append(f"Cobalt: {exc}")
            update_job(job_id, message="تعذرت الخوادم المجانية. نجرب المسار الاحتياطي.")

    if is_yt and ENABLE_TUNELIO_FALLBACK and TUNELIO_API_KEY and requests is not None:
        try:
            media_path, title = download_via_tunelio(job_id, url, workdir)
            update_job(
                job_id,
                original_path=str(media_path),
                title=title,
                status="extracting",
                stage="استخراج الصوت",
                progress=24,
                message="اكتمل التحميل السريع. نستخرج المسار الصوتي الآن.",
            )
            return media_path
        except LinkDurationLimitError:
            raise
        except Exception as exc:
            download_errors.append(f"Tunelio: {exc}")
            update_job(job_id, message="تعذر خادم التنزيل الاحتياطي. نجرب التنزيل المباشر إن توفر.")

    if is_yt and yt_dlp is not None and not HOSTED_SPACE:
        for clients in youtube_download_clients(url):
            cleanup_partial_downloads(workdir)
            label = "، ".join(clients) if clients else "عام"
            update_job(job_id, message=f"نحاول تنزيل الرابط عبر قناة آمنة: {label}.")
            try:
                with yt_dlp.YoutubeDL(build_ydl_options(workdir, job_id, clients)) as ydl:
                    info = ydl.extract_info(url, download=True)
                break
            except Exception as exc:
                download_errors.append(f"{label}: {exc}")
                update_job(job_id, message=f"تعذر التنزيل عبر {label}. نجرب قناة أخرى إن توفرت.")
                time.sleep(1)

    if info is not None:
        media_path = find_downloaded_media(workdir)
        if not media_path:
            raise RuntimeError("اكتمل التحميل لكن لم نستطع تحديد ملف الوسائط الناتج.")
        title = info.get("title") or "مقطع من رابط"
    else:
        if is_yt:
            raise RuntimeError(youtube_download_error(download_errors))

        update_job(job_id, message="رابط خارجي. نحاول التنزيل عبر الخوادم المساندة...")
        try:
            media_path = download_via_cobalt(job_id, url, workdir)
            title = media_path.name
        except Exception as exc:
            all_errors = download_errors + [str(exc)]
            raise RuntimeError(youtube_download_error(all_errors))

    update_job(
        job_id,
        original_path=str(media_path),
        title=title,
        status="extracting",
        stage="استخراج الصوت",
        progress=24,
        message="اكتمل التحميل. نستخرج المسار الصوتي الآن.",
    )
    return media_path


def youtube_download_clients(url: str) -> tuple[tuple[str, ...], ...]:
    if is_youtube_url(url):
        return YOUTUBE_CLIENT_FALLBACKS
    return ((),)


def is_youtube_url(url: str) -> bool:
    lowered = url.lower()
    return "youtube.com" in lowered or "youtu.be" in lowered


def coerce_duration_seconds(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds > 0 else None

    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        try:
            parts = [float(part) for part in text.split(":")]
        except ValueError:
            parts = []
        if parts:
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60 + part
            return seconds if math.isfinite(seconds) and seconds > 0 else None

    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    seconds = float(match.group(0))
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def format_duration_ar(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, rest = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}:{rest:02d} دقيقة"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{rest:02d} ساعة"


def enforce_link_duration_seconds(duration_seconds: Optional[float], source_label: str = "الرابط") -> None:
    if MAX_LINK_DURATION_SECONDS <= 0 or duration_seconds is None:
        return
    if duration_seconds <= MAX_LINK_DURATION_SECONDS:
        return
    raise LinkDurationLimitError(
        f"مدة {source_label} {format_duration_ar(duration_seconds)}، والحد الأقصى للروابط هو "
        f"{format_duration_ar(MAX_LINK_DURATION_SECONDS)} حتى لا يتراكم طابور التنقية. "
        "اختر مقطعاً أقصر أو قصّ الرابط ثم أعد المحاولة."
    )


def probe_media_duration_seconds(path: Path) -> Optional[float]:
    ffmpeg = require_ffmpeg()
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    details = "\n".join(part for part in [result.stdout, result.stderr] if part)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", details)
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def enforce_link_file_duration(job_id: str, media_path: Path) -> None:
    duration_seconds = probe_media_duration_seconds(media_path)
    if duration_seconds is not None:
        update_job(job_id, duration_seconds=round(duration_seconds, 2))
    enforce_link_duration_seconds(duration_seconds, "الرابط")


def build_ydl_options(workdir: Path, job_id: str, youtube_clients: tuple[str, ...]) -> Dict[str, Any]:
    ydl_opts: Dict[str, Any] = {
        "outtmpl": str(workdir / "source.%(ext)s"),
        "format": "b[height<=720]/best[height<=720]/b/bv*[height<=720]+ba/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "retries": YOUTUBE_RETRIES,
        "fragment_retries": YOUTUBE_RETRIES,
        "extractor_retries": YOUTUBE_RETRIES,
        "socket_timeout": YOUTUBE_SOCKET_TIMEOUT,
        "continuedl": False,
        "overwrites": True,
        "nopart": True,
        "cachedir": False,
        "source_address": "0.0.0.0",
        "ffmpeg_location": str(Path(require_ffmpeg()).parent),
        "remote_components": list(YOUTUBE_REMOTE_COMPONENTS),
        "max_filesize": MAX_REMOTE_DOWNLOAD_BYTES,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            )
        },
        "progress_hooks": [lambda data: yt_progress_hook(job_id, data)],
    }
    if YTDLP_PROXY:
        ydl_opts["proxy"] = YTDLP_PROXY
    extractor_args: Dict[str, Dict[str, list[str]]] = {}
    youtube_args: Dict[str, list[str]] = {}
    if youtube_clients:
        youtube_args["player_client"] = list(youtube_clients)
    if YOUTUBE_FETCH_POT:
        youtube_args["fetch_pot"] = [YOUTUBE_FETCH_POT]
    if youtube_args:
        extractor_args["youtube"] = youtube_args
    if YOUTUBE_POT_BASE_URL:
        extractor_args["youtubepot-bgutilhttp"] = {"base_url": [YOUTUBE_POT_BASE_URL]}
    if extractor_args:
        ydl_opts["extractor_args"] = extractor_args
    if YTDLP_COOKIES:
        cookie_path = Path(YTDLP_COOKIES)
        if cookie_path.exists():
            ydl_opts["cookiefile"] = str(cookie_path)
    return ydl_opts


def cleanup_partial_downloads(workdir: Path) -> None:
    for pattern in ("source.*.part", "source.*.ytdl", "source.*.temp", "source.*.tmp"):
        for path in workdir.glob(pattern):
            try:
                path.unlink()
            except OSError:
                pass


def youtube_download_error(errors: list[str]) -> str:
    last_error = errors[-1] if errors else "لم يصل تفصيل من yt-dlp."
    if "Sign in to confirm" in last_error or "not a bot" in last_error or "cookies" in last_error.lower():
        if YTDLP_PROXY:
            return (
                "طلب YouTube إثبات أن الخادم ليس روبوتاً. ملف cookies أو البروكسي الحالي غير كافيين لهذا الرابط."
            )
        return (
            "طلب YouTube إثبات أن الخادم ليس روبوتاً. نحتاج ملف cookies صالحاً وبروكسي نظيفاً لتفعيل تنزيل هذا الرابط بثبات."
        )
    if "UNEXPECTED_EOF_WHILE_READING" in last_error or "SSL" in last_error:
        return (
            "تعذر تنزيل الرابط من YouTube داخل الاستضافة بسبب انقطاع اتصال SSL. "
            "حدّثنا الخادم ليجرب عدة قنوات تلقائياً، لكن إن تكرر الخطأ فغالباً أن الاستضافة تقطع اتصال YouTube مؤقتاً. "
            "جرّب مرة أخرى، أو ارفع الملف من جهازك عبر تبويب ملف."
        )
    if "HTTP Error 403" in last_error or "Forbidden" in last_error:
        return "منع YouTube تنزيل هذا الرابط مؤقتاً من خادم الاستضافة. جرّب رابطاً آخر أو ارفع الملف من جهازك."
    return "تعذر تنزيل الرابط بعد عدة محاولات. جرّب رابطاً آخر أو ارفع الملف من جهازك."


def yt_progress_hook(job_id: str, data: Dict[str, Any]) -> None:
    if data.get("status") != "downloading":
        return
    total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
    downloaded = data.get("downloaded_bytes") or 0
    if total:
        progress = 8 + min(14, int((downloaded / total) * 14))
        update_job(job_id, progress=progress, message=f"جار التحميل: {progress}% من مرحلة التحميل.")


def extract_audio(job_id: str, original: Path) -> Path:
    ffmpeg = require_ffmpeg()
    audio = job_dir(job_id) / "analysis.wav"
    update_job(job_id, status="extracting", stage="استخراج الصوت", progress=30, message="نحوّل الصوت إلى صيغة مناسبة للتحليل.")
    run_cmd(
        [
            ffmpeg,
            "-y",
            "-i",
            str(original),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-sample_fmt",
            "s16",
            str(audio),
        ],
        "فشل استخراج الصوت من المقطع.",
        job_id=job_id,
    )
    if not audio.exists() or audio.stat().st_size == 0:
        raise RuntimeError("فشل استخراج الصوت: لم ينتج ffmpeg ملف التحليل الصوتي.")
    update_job(job_id, audio_path=str(audio))
    return audio


def separate_vocals(job_id: str, audio: Path, quality: str = "high") -> tuple[Path, Path]:
    if not has_module("demucs"):
        raise RuntimeError("مكتبة demucs غير مثبتة. ثبّت المتطلبات ثم أعد تشغيل السيرفر.")
    if not audio.exists() or audio.stat().st_size == 0:
        raise RuntimeError("ملف الصوت المطلوب للعزل غير موجود. أعد المحاولة بعد إعادة تشغيل الخادم.")

    out_dir = job_dir(job_id) / "separated"
    
    # Select Demucs model based on requested quality
    model = DEMUCS_MODEL  # htdemucs - htdemucs_ft crashes T4 GPU with OOM
    if quality == "fast":
        model = "hdemucs_mmi"
        
    msg = "نعزل الصوت البشري عن مسار المعازف بالذكاء الاصطناعي. المقاطع الصعبة قد تحتاج عدة دقائق للحفاظ على الجودة."
        
    update_job(job_id, status="separating", stage="عزل الصوت", progress=42, message=msg)
    shifts = 1 if quality == "fast" else DEMUCS_SHIFTS
    overlap = 0.5 if quality == "fast" else DEMUCS_OVERLAP
    requested_segment = DEMUCS_SEGMENT if DEMUCS_SEGMENT > 0 else 7
    segment = max(1, int(min(float(requested_segment), float(DEMUCS_MAX_SEGMENT))))
    command = [
        sys.executable,
        "-m",
        "demucs.separate",
        "--two-stems",
        "vocals",
        "-n",
        model,
        "-j",
        str(max(1, DEMUCS_JOBS)),
        "--shifts",
        str(shifts),
        "--overlap",
        str(overlap),
        "--segment",
        str(segment),
    ]
    if HAS_GPU:
        command.extend(["-d", "cuda"])
    command.extend([
        "-o",
        str(out_dir),
        str(audio),
    ])
    run_cmd(command, "فشل محرك عزل الصوت.", job_id=job_id)

    vocals, instrumental = find_demucs_stems(out_dir, audio.stem, model)
    if not vocals.exists() or not instrumental.exists():
        raise RuntimeError("انتهى محرك العزل لكن ملفات الصوت المتوقعة غير موجودة.")

    update_job(job_id, status="analyzing", stage="تحليل النتيجة", progress=68, message="نقيس أثر الطبقة غير الصوتية قبل إصدار الحكم.")
    return vocals, instrumental


def find_demucs_stems(out_dir: Path, stem_name: str, model_name: str = None) -> tuple[Path, Path]:
    expected_root = out_dir / (model_name or DEMUCS_MODEL) / stem_name
    vocals = expected_root / "vocals.wav"
    instrumental = expected_root / "no_vocals.wav"
    if vocals.exists() and instrumental.exists():
        return vocals, instrumental

    vocals_matches = list(out_dir.glob(f"**/{stem_name}/vocals.wav")) + list(out_dir.glob("**/vocals.wav"))
    instrumental_matches = list(out_dir.glob(f"**/{stem_name}/no_vocals.wav")) + list(out_dir.glob("**/no_vocals.wav"))
    if vocals_matches and instrumental_matches:
        return vocals_matches[0], instrumental_matches[0]

    return vocals, instrumental


def is_voiceless_music(ratio: float) -> bool:
    return ratio >= VOICELESS_MUSIC_RATIO_THRESHOLD


def silence_result_for_voiceless_music(job_id: str, reference_audio: Path) -> Dict[str, Any]:
    out = write_silence_like(reference_audio, job_dir(job_id) / "vocals_silenced_no_voice.wav")
    return {
        "path": out,
        "mode": "silence_no_voice",
        "ratio": 0.0,
        "absolute": 0.0,
        "safe": True,
        "voiceless": True,
    }


def silence_result_for_persistent_music(job_id: str, reference_audio: Path, best: Dict[str, Any]) -> Dict[str, Any]:
    out = write_silence_like(reference_audio, job_dir(job_id) / "vocals_silenced_persistent_music.wav")
    return {
        "path": out,
        "mode": "silence_persistent_music",
        "ratio": float(best.get("ratio") or 0.0),
        "absolute": float(best.get("absolute") or 0.0),
        "safe": True,
        "silenced": True,
    }


def speech_rescue_result_for_persistent_music(
    job_id: str,
    vocals_path: Path,
    instrumental_path: Path,
    best: Dict[str, Any],
) -> Dict[str, Any]:
    candidate = purify_vocal_stem(vocals_path, instrumental_path, job_id, mode="speech_rescue")
    ratio, absolute = estimate_residual_music_bleed(candidate, instrumental_path)
    return {
        "path": candidate,
        "mode": "speech_rescue",
        "ratio": ratio,
        "absolute": absolute,
        "safe": ratio < RESIDUAL_MUSIC_RATIO_THRESHOLD or absolute < RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD,
        "speech_rescue": True,
        "previous_ratio": float(best.get("ratio") or 0.0),
    }


def uvr_rescue_result_for_persistent_music(
    job_id: str,
    source_audio: Path,
    vocals_path: Path,
    instrumental_path: Path,
    best: Dict[str, Any],
) -> Dict[str, Any]:
    if not UVR_RESCUE_ENABLED:
        raise RuntimeError("UVR rescue is disabled.")

    update_job(
        job_id,
        progress=88,
        purification_mode="roformer_raw",
        message="نشغّل RoFormer لعزل المعازف مع إبقاء مسار الصوت البشري الخام.",
    )
    candidates: list[tuple[Path, float, float, str]] = []
    for index, model_filename in enumerate(UVR_RESCUE_MODELS):
        try:
            update_job(job_id, message=f"نشغّل نموذج RoFormer ({index + 1}/{len(UVR_RESCUE_MODELS)}) مع إبقاء الصوت خاماً.")
            uvr_vocals = separate_vocals_with_uvr(job_id, source_audio, model_filename, index)
            ratio, absolute = estimate_residual_music_bleed(uvr_vocals, instrumental_path)
            candidates.append((uvr_vocals, ratio, absolute, "roformer_raw"))
            if best_candidate_is_strict_safe(candidates):
                break
        except Exception:
            continue

    if not candidates:
        raise RuntimeError("انتهى نموذج UVR بدون إنتاج مرشح صوتي صالح.")

    path, ratio, absolute, mode = min(candidates, key=lambda item: (item[1], item[2]))
    update_job(job_id, residual_music_ratio=round(ratio, 4), purification_mode=mode)
    return {
        "path": path,
        "mode": mode,
        "ratio": ratio,
        "absolute": absolute,
        "safe": ratio < STRICT_RESIDUAL_MUSIC_RATIO_THRESHOLD or absolute < STRICT_RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD,
        "uvr_rescue": True,
        "natural_voice": True,
        "previous_ratio": float(best.get("ratio") or 0.0),
    }


def add_rescue_candidates(
    candidates: list[tuple[Path, float, float, str]],
    job_id: str,
    vocals_path: Path,
    instrumental_path: Path,
    mode_prefix: str,
) -> None:
    raw_ratio, raw_absolute = estimate_residual_music_bleed(vocals_path, instrumental_path)
    candidates.append((vocals_path, raw_ratio, raw_absolute, mode_prefix))
    raw_rms = max(rms_wav(vocals_path), 1.0)
    for mode in ("uvr_cleanup", "speech_rescue", "speech_rescue_hard"):
        try:
            cleaned = purify_vocal_stem(vocals_path, instrumental_path, job_id, mode=mode)
            cleaned_ratio, cleaned_absolute = estimate_residual_music_bleed(cleaned, instrumental_path)
            cleaned_rms = rms_wav(cleaned)
            if cleaned_rms >= raw_rms * 0.16:
                candidates.append((cleaned, cleaned_ratio, cleaned_absolute, f"{mode_prefix}_{mode}"))
        except Exception:
            continue


def best_candidate_is_strict_safe(candidates: list[tuple[Path, float, float, str]]) -> bool:
    if not candidates:
        return False
    _, ratio, absolute, _ = min(candidates, key=lambda item: (item[1], item[2]))
    return ratio < STRICT_RESIDUAL_MUSIC_RATIO_THRESHOLD or absolute < STRICT_RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD


def separate_vocals_with_uvr(job_id: str, source_audio: Path, model_filename: str, index: int) -> Path:
    import logging
    from audio_separator.separator import Separator

    out_dir = job_dir(job_id) / f"uvr_{index}"
    out_dir.mkdir(parents=True, exist_ok=True)
    separator = Separator(
        log_level=logging.WARNING,
        model_file_dir=UVR_MODEL_DIR,
        output_dir=str(out_dir),
        output_format="WAV",
        output_single_stem="Vocals",
        normalization_threshold=0.92,
        sample_rate=44100,
        mdxc_params={"segment_size": 256, "batch_size": 1, "overlap": 8},
    )
    separator.load_model(model_filename=model_filename)
    output_files = separator.separate(str(source_audio))
    candidates = []
    for item in output_files:
        path = Path(item)
        if not path.is_absolute():
            path = out_dir / path
        if path.exists() and path.suffix.lower() == ".wav":
            candidates.append(path)
    candidates.extend(path for path in out_dir.glob("*.wav") if "vocal" in path.name.lower())
    if not candidates:
        raise RuntimeError("انتهى نموذج UVR بدون إنتاج مسار صوت بشري.")
    return sorted(candidates, key=lambda path: ("vocal" not in path.name.lower(), len(path.name)))[0]


def write_silence_like(reference_audio: Path, out: Path) -> Path:
    try:
        import numpy as np
        import soundfile as sf
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("تنقص مكتبات تجهيز مسار الصمت: numpy و soundfile.") from exc

    with sf.SoundFile(str(reference_audio), "r") as source:
        with sf.SoundFile(
            str(out),
            "w",
            samplerate=source.samplerate,
            channels=source.channels,
            subtype="PCM_16",
        ) as target:
            remaining = len(source)
            block_size = 65536
            while remaining > 0:
                frames = min(block_size, remaining)
                target.write(np.zeros((frames, source.channels), dtype=np.float32))
                remaining -= frames
    return out


def purify_with_retries(job_id: str, source_audio: Path, vocals_path: Path, instrumental_path: Path, source_ratio: float = 0.0) -> Dict[str, Any]:
    raw_ratio, raw_absolute = estimate_residual_music_bleed(vocals_path, instrumental_path)
    demucs_result = {
        "path": vocals_path,
        "mode": "demucs_raw_fallback",
        "ratio": raw_ratio,
        "absolute": raw_absolute,
        "safe": raw_ratio < RESIDUAL_MUSIC_RATIO_THRESHOLD or raw_absolute < RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD,
        "natural_voice": True,
    }
    update_job(
        job_id,
        progress=86,
        message="نعزل المعازف بنموذج RoFormer مع إبقاء الصوت البشري بلا فلاتر إضافية.",
        residual_music_ratio=round(raw_ratio, 4),
        purification_mode=demucs_result["mode"],
    )
    try:
        return uvr_rescue_result_for_persistent_music(
            job_id,
            source_audio,
            vocals_path,
            instrumental_path,
            demucs_result,
        )
    except Exception:
        return demucs_result


def completion_message(purification_result: Optional[Dict[str, Any]]) -> str:
    if not purification_result:
        return "تم بحمد الله عزل مسار المعازف وإعداد نسخة منقّاة قدر الإمكان. الملف جاهز للتحميل."
    if purification_result.get("voiceless"):
        return "لم نجد صوتاً بشرياً موثوقاً بعد عزل المعازف، لذلك أخرجنا مساراً صامتاً بدل تمرير الموسيقى."
    if purification_result.get("silenced"):
        return "بقي أثر موسيقي واضح بعد أقوى تنقية، لذلك أخرجنا مساراً صامتاً احتياطاً بدل تمرير المعازف."
    if purification_result.get("speech_rescue"):
        return "بقي أثر موسيقي بعد أقوى تنقية، فشغلنا وضع إنقاذ الكلام بفلترة أشد بدل كتم الصوت كاملاً."
    if purification_result.get("uvr_rescue"):
        return "تم عزل المعازف بنموذج RoFormer مع إبقاء مسار الصوت البشري خاماً بلا فلاتر تغيّر طبيعته."
    if purification_result.get("natural_voice"):
        return "تم عزل المعازف مع إبقاء مسار الصوت البشري خاماً بلا فلاتر تغيّر طبيعته."
    if purification_result.get("safe"):
        return "تم بحمد الله عزل مسار المعازف وإعداد نسخة منقّاة قدر الإمكان. الملف جاهز للتحميل."
    return "اكتملت أقوى محاولة تنقية متاحة. قد يتأثر الصوت البشري، لكننا أعدنا المحاولة لتقليل بقايا المعازف قدر الإمكان."


def purification_profile(mode: str) -> Dict[str, Any]:
    profiles = {
        "balanced": {
            "message": "نزيل بقايا المعازف من مسار الكلام مع الحفاظ على وضوح الصوت.",
            "subtract_base": 0.55,
            "subtract_bin": 0.75,
            "subtract_frame": 0.65,
            "subtract_share": 0.55,
            "floor_base": 0.015,
            "floor_voice": 0.16,
            "dominant_share": 0.16,
            "dominant_voice": 0.10,
            "dominant_cap": 0.08,
            "confident_share": 0.28,
            "confident_voice": 0.20,
            "confident_floor": 0.80,
        },
        "strong": {
            "message": "بقي أثر موسيقي، نعيد التنقية بنمط أقوى.",
            "subtract_base": 0.85,
            "subtract_bin": 1.05,
            "subtract_frame": 0.90,
            "subtract_share": 0.75,
            "floor_base": 0.004,
            "floor_voice": 0.08,
            "dominant_share": 0.22,
            "dominant_voice": 0.16,
            "dominant_cap": 0.04,
            "confident_share": 0.38,
            "confident_voice": 0.32,
            "confident_floor": 0.65,
        },
        "extreme": {
            "message": "نستخدم أقوى نمط متاح لإزالة بقايا العزف، وقد يتأثر الصوت البشري.",
            "subtract_base": 1.15,
            "subtract_bin": 1.35,
            "subtract_frame": 1.10,
            "subtract_share": 0.90,
            "floor_base": 0.001,
            "floor_voice": 0.035,
            "dominant_share": 0.30,
            "dominant_voice": 0.25,
            "dominant_cap": 0.015,
            "confident_share": 0.48,
            "confident_voice": 0.45,
            "confident_floor": 0.45,
        },
        "uvr_cleanup": {
            "message": "نخفف بقايا العزف بعد نموذج UVR بدون إسقاط الصوت البشري.",
            "subtract_base": 1.05,
            "subtract_bin": 1.25,
            "subtract_frame": 1.00,
            "subtract_share": 0.85,
            "floor_base": 0.004,
            "floor_voice": 0.055,
            "dominant_share": 0.32,
            "dominant_voice": 0.28,
            "dominant_cap": 0.025,
            "confident_share": 0.52,
            "confident_voice": 0.50,
            "confident_floor": 0.48,
        },
        "speech_rescue": {
            "message": "نستخدم وضع إنقاذ الكلام بفلترة أشد بدل كتم الصوت كاملاً.",
            "subtract_base": 1.75,
            "subtract_bin": 2.20,
            "subtract_frame": 1.55,
            "subtract_share": 1.30,
            "floor_base": 0.0,
            "floor_voice": 0.0,
            "dominant_share": 0.52,
            "dominant_voice": 0.45,
            "dominant_cap": 0.0,
            "confident_share": 0.68,
            "confident_voice": 0.70,
            "confident_floor": 0.34,
            "speech_rescue": True,
        },
        "speech_rescue_hard": {
            "message": "نستخدم وضع إنقاذ الكلام الأشد للحالات التي تشبه فيها الآلة الصوت البشري.",
            "subtract_base": 2.05,
            "subtract_bin": 2.55,
            "subtract_frame": 1.75,
            "subtract_share": 1.50,
            "floor_base": 0.0,
            "floor_voice": 0.0,
            "dominant_share": 0.60,
            "dominant_voice": 0.52,
            "dominant_cap": 0.0,
            "confident_share": 0.78,
            "confident_voice": 0.80,
            "confident_floor": 0.26,
            "speech_rescue": True,
        },
    }
    profile = profiles.get(mode, profiles["balanced"]).copy()
    profile.setdefault("speech_rescue", False)
    return profile


def purify_vocal_stem(vocals_path: Path, instrumental_path: Path, job_id: str, mode: str = "balanced") -> Path:
    """Reduce musical bleed inside the Demucs vocal stem without hard-cutting speech."""
    try:
        import numpy as np
        import soundfile as sf
    except Exception as exc:  # pragma: no cover - reported as a runtime dependency error
        raise RuntimeError("تنقص مكتبات تنقية الصوت المتقدمة: numpy و soundfile.") from exc

    vocals, sr = sf.read(str(vocals_path), always_2d=True, dtype="float32")
    instrumental, instrumental_sr = sf.read(str(instrumental_path), always_2d=True, dtype="float32")
    if sr != instrumental_sr:
        raise RuntimeError("تعذر تنقية الصوت: معدل العينة بين مساري العزل غير متطابق.")

    length = min(len(vocals), len(instrumental))
    if length <= 0:
        raise RuntimeError("تعذر تنقية الصوت: خرج العزل فارغ.")

    profile = purification_profile(mode)
    vocals = vocals[:length]
    instrumental = match_audio_channels(instrumental[:length], vocals.shape[1])

    chunk_seconds = max(10.0, float(os.getenv("HALALSTREAM_SPECTRAL_CHUNK_SECONDS", "30")))
    chunk_len = max(sr * 5, int(sr * chunk_seconds))
    overlap_len = min(sr, max(1, chunk_len // 4))
    step = max(1, chunk_len - overlap_len)

    purified = np.zeros_like(vocals, dtype=np.float32)
    weights = np.zeros((length, 1), dtype=np.float32)

    start = 0
    while start < length:
        end = min(length, start + chunk_len)
        chunk_out = np.empty((end - start, vocals.shape[1]), dtype=np.float32)
        for channel in range(vocals.shape[1]):
            chunk_out[:, channel] = suppress_music_leakage(
                vocals[start:end, channel],
                instrumental[start:end, channel],
                sr,
                profile,
            )

        weight = chunk_weight(end - start, overlap_len, fade_in=start > 0, fade_out=end < length)
        purified[start:end] += chunk_out * weight
        weights[start:end] += weight
        if end >= length:
            break
        start += step

    purified = purified / np.maximum(weights, 1e-6)
    purified = soft_limit_audio(purified)

    out = vocals_path.parent / f"vocals_purified_{mode}.wav"
    sf.write(str(out), purified, sr, subtype="PCM_16")
    return out


def match_audio_channels(audio, channels: int):
    import numpy as np

    if audio.ndim == 1:
        audio = audio[:, None]
    if audio.shape[1] == channels:
        return audio
    if audio.shape[1] == 1:
        return np.repeat(audio, channels, axis=1)
    if channels == 1:
        return np.mean(audio, axis=1, keepdims=True)
    return audio[:, :channels]


def chunk_weight(length: int, overlap_len: int, fade_in: bool, fade_out: bool):
    import numpy as np

    weight = np.ones((length, 1), dtype=np.float32)
    fade = min(overlap_len, length // 2)
    if fade_in and fade > 1:
        weight[:fade, 0] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
    if fade_out and fade > 1:
        weight[-fade:, 0] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return weight


def suppress_music_leakage(vocal_chunk, instrumental_chunk, sr: int, profile: Dict[str, Any]):
    import numpy as np

    if len(vocal_chunk) == 0:
        return vocal_chunk.astype(np.float32)

    n_fft = 2048
    hop = 512
    eps = 1e-8

    vocal_spec, total_len, pad = stft_np(vocal_chunk, n_fft=n_fft, hop=hop)
    instrumental_spec, _, _ = stft_np(instrumental_chunk, n_fft=n_fft, hop=hop)
    vocal_mag = np.abs(vocal_spec).astype(np.float32)
    instrumental_mag = np.abs(instrumental_spec).astype(np.float32)

    vocal_frame = np.sqrt(np.mean(vocal_mag * vocal_mag, axis=1) + eps)
    instrumental_frame = np.sqrt(np.mean(instrumental_mag * instrumental_mag, axis=1) + eps)
    instrumental_share = instrumental_frame / (vocal_frame + instrumental_frame + eps)
    voice_share = vocal_frame / (vocal_frame + instrumental_frame + eps)

    bin_instrumental_share = instrumental_mag / (vocal_mag + instrumental_mag + eps)
    subtract_scale = (
        profile["subtract_base"] + profile["subtract_bin"] * bin_instrumental_share
    ) * (
        profile["subtract_frame"] + profile["subtract_share"] * instrumental_share[:, None]
    )
    spectral_floor = profile["floor_base"] + profile["floor_voice"] * voice_share[:, None] * (1.0 - bin_instrumental_share)
    cleaned_mag = np.maximum(
        vocal_mag - instrumental_mag * subtract_scale,
        vocal_mag * spectral_floor,
    )

    phase = vocal_spec / (vocal_mag + eps)
    cleaned = istft_np(cleaned_mag * phase, total_len=total_len, pad=pad, original_len=len(vocal_chunk), n_fft=n_fft, hop=hop)
    frame_gain = voice_activity_gain(vocal_frame, instrumental_frame, profile)
    cleaned *= interpolate_frame_gain(frame_gain, len(cleaned), hop)
    return np.clip(cleaned, -1.0, 1.0).astype(np.float32)


def stft_np(signal, n_fft: int, hop: int):
    import numpy as np

    signal = np.asarray(signal, dtype=np.float32)
    pad = n_fft // 2
    pad_mode = "reflect" if len(signal) > pad else "constant"
    padded = np.pad(signal, (pad, pad), mode=pad_mode)
    frames = int(np.ceil(max(0, len(padded) - n_fft) / hop)) + 1
    total_len = (frames - 1) * hop + n_fft
    if total_len > len(padded):
        padded = np.pad(padded, (0, total_len - len(padded)), mode="constant")

    window = np.hanning(n_fft).astype(np.float32)
    spec = np.empty((frames, n_fft // 2 + 1), dtype=np.complex64)
    for idx in range(frames):
        start = idx * hop
        spec[idx] = np.fft.rfft(padded[start:start + n_fft] * window)
    return spec, total_len, pad


def istft_np(spec, total_len: int, pad: int, original_len: int, n_fft: int, hop: int):
    import numpy as np

    window = np.hanning(n_fft).astype(np.float32)
    output = np.zeros(total_len, dtype=np.float32)
    norm = np.zeros(total_len, dtype=np.float32)
    for idx in range(spec.shape[0]):
        start = idx * hop
        frame = np.fft.irfft(spec[idx], n=n_fft).astype(np.float32) * window
        output[start:start + n_fft] += frame
        norm[start:start + n_fft] += window * window

    active = norm > 1e-8
    output[active] /= norm[active]
    return output[pad:pad + original_len]


def voice_activity_gain(vocal_frame, instrumental_frame, profile: Dict[str, Any]):
    import numpy as np

    eps = 1e-8
    nonzero = vocal_frame[vocal_frame > eps]
    reference = float(np.percentile(nonzero, 90)) if nonzero.size else 1.0
    absolute_voice = vocal_frame / (reference + eps)
    share = vocal_frame / (vocal_frame + instrumental_frame + eps)

    share_gain = np.clip((share - 0.05) / 0.22, 0.0, 1.0)
    level_gain = np.clip((absolute_voice - 0.025) / 0.16, 0.0, 1.0)
    gain = np.maximum(share_gain, level_gain)
    if profile.get("speech_rescue"):
        strong_share = share > 0.58
        confident_share = share > 0.72
        gain = np.where(strong_share, gain, gain * 0.05)
        gain = np.where(confident_share, np.maximum(gain, 0.36), gain)

    dominant_music = (share < profile["dominant_share"]) & (absolute_voice < profile["dominant_voice"])
    gain[dominant_music] = np.minimum(gain[dominant_music], profile["dominant_cap"])

    confident_voice = (share > profile["confident_share"]) | (absolute_voice > profile["confident_voice"])
    gain[confident_voice] = np.maximum(gain[confident_voice], profile["confident_floor"])

    gain = max_filter_1d(np.clip(gain, 0.0, 1.0), radius=3)
    gain = smooth_1d(gain, radius=6)
    return gain.astype(np.float32)


def interpolate_frame_gain(frame_gain, length: int, hop: int):
    import numpy as np

    if len(frame_gain) == 1:
        return np.full(length, frame_gain[0], dtype=np.float32)
    centers = np.arange(len(frame_gain), dtype=np.float32) * hop
    samples = np.arange(length, dtype=np.float32)
    return np.interp(samples, centers, frame_gain, left=frame_gain[0], right=frame_gain[-1]).astype(np.float32)


def max_filter_1d(values, radius: int):
    import numpy as np

    if radius <= 0 or len(values) == 0:
        return values
    padded = np.pad(values, (radius, radius), mode="edge")
    stacked = [padded[offset:offset + len(values)] for offset in range(radius * 2 + 1)]
    return np.max(np.vstack(stacked), axis=0)


def smooth_1d(values, radius: int):
    import numpy as np

    if radius <= 0 or len(values) == 0:
        return values
    kernel = np.ones(radius * 2 + 1, dtype=np.float32)
    kernel /= np.sum(kernel)
    padded = np.pad(values, (radius, radius), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def soft_limit_audio(audio):
    import numpy as np

    audio = np.asarray(audio, dtype=np.float32)
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.98:
        audio = audio * (0.98 / peak)
    return np.clip(audio, -0.98, 0.98).astype(np.float32)


def verify_purified_audio(job_id: str, purified_vocals: Path, instrumental_path: Path) -> None:
    ratio, absolute = estimate_residual_music_bleed(purified_vocals, instrumental_path)
    update_job(job_id, residual_music_ratio=round(ratio, 4))
    if ratio >= RESIDUAL_MUSIC_RATIO_THRESHOLD and absolute >= RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD:
        raise RuntimeError(
            "بقي أثر موسيقي واضح بعد التنقية، لذلك أوقفنا إخراج الملف احتياطاً. "
            "هذا المقطع يحتاج نموذج عزل أقوى أو معالجة يدوية."
        )


def estimate_residual_music_bleed(purified_vocals: Path, instrumental_path: Path) -> tuple[float, float]:
    try:
        import numpy as np
        import soundfile as sf
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("تنقص مكتبات فحص الصوت بعد التنقية: numpy و soundfile.") from exc

    clean, sr = sf.read(str(purified_vocals), always_2d=True, dtype="float32")
    instrumental, inst_sr = sf.read(str(instrumental_path), always_2d=True, dtype="float32")
    if sr != inst_sr:
        return 0.0, 0.0

    length = min(len(clean), len(instrumental))
    if length <= 0:
        return 0.0, 0.0

    clean = np.mean(clean[:length], axis=1).astype(np.float32)
    instrumental = np.mean(instrumental[:length], axis=1).astype(np.float32)
    n_fft = 2048
    hop = 512
    clean_spec, _, _ = stft_np(clean, n_fft=n_fft, hop=hop)
    inst_spec, _, _ = stft_np(instrumental, n_fft=n_fft, hop=hop)
    clean_mag = np.abs(clean_spec).astype(np.float32)
    inst_mag = np.abs(inst_spec).astype(np.float32)
    shared = np.minimum(clean_mag, inst_mag)
    ratio = float(np.sum(shared) / max(np.sum(clean_mag), 1e-8))
    clean_rms = float(np.sqrt(np.mean(np.square(clean))))
    absolute = clean_rms * ratio
    return ratio, absolute


def encode_audio(
    job_id: str,
    source_audio: Path,
    filename: str,
    progress: int,
    message: str,
    filter_vocals: bool = False,
    speech_only: bool = False,
) -> Path:
    ffmpeg = require_ffmpeg()
    out = job_dir(job_id) / filename
    update_job(job_id, progress=progress, message=message)
    
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(source_audio),
        "-vn",
    ]
    audio_filter = output_audio_filter(filter_vocals, speech_only)
    if audio_filter:
        cmd.extend(["-af", audio_filter])
        
    cmd.extend([
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(out),
    ])
    
    run_cmd(
        cmd,
        "فشل تجهيز ملف الصوت.",
        job_id=job_id,
    )
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("فشل تجهيز ملف الصوت: لم ينتج ffmpeg ملفاً صالحاً.")
    return out


def output_audio_filter(filter_vocals: bool, speech_only: bool) -> str:
    if not filter_vocals:
        return ""
    if VOICE_ENHANCE_ENABLED:
        return VOICE_ENHANCE_SPEECH_FILTER if speech_only else VOICE_ENHANCE_FILTER
    if speech_only:
        return "highpass=f=120,lowpass=f=4200,afftdn=nf=-25"
    return "highpass=f=70,lowpass=f=9500"


def estimate_music_ratio(audio: Path, vocals: Path, instrumental: Path) -> tuple[float, float]:
    total = rms_wav(audio)
    vocal_rms = rms_wav(vocals)
    instrumental_rms = rms_wav(instrumental)
    denominator = max(vocal_rms + instrumental_rms, total, 1.0)
    raw_ratio = instrumental_rms / denominator
    normalized_instrumental = instrumental_rms / 32768.0
    ratio = raw_ratio
    if normalized_instrumental < MUSIC_ABSOLUTE_RMS_THRESHOLD:
        ratio = min(raw_ratio, MUSIC_RATIO_THRESHOLD * 0.75)
    confidence = min(0.98, 0.55 + abs(ratio - MUSIC_RATIO_THRESHOLD) * 2.5)
    return ratio, confidence


def rms_wav(path: Path) -> float:
    import audioop

    with wave.open(str(path), "rb") as wav:
        width = wav.getsampwidth()
        frames = wav.getnframes()
        if frames == 0:
            return 0.0
        chunk_size = 44100 * 6
        total_square = 0.0
        total_samples = 0
        while True:
            data = wav.readframes(chunk_size)
            if not data:
                break
            rms = audioop.rms(data, width)
            samples = len(data) / max(width, 1)
            total_square += (rms * rms) * samples
            total_samples += samples
    if not total_samples:
        return 0.0
    return math.sqrt(total_square / total_samples)


def ensure_runtime() -> None:
    missing = []
    if yt_dlp is None:
        missing.append("yt-dlp")
    if not get_ffmpeg_path():
        missing.append("ffmpeg")
    if not has_module("demucs"):
        missing.append("demucs")
    if not has_module("numpy"):
        missing.append("numpy")
    if not has_module("soundfile"):
        missing.append("soundfile")
    if missing:
        raise RuntimeError("تنقص بيئة المعالجة: " + "، ".join(missing))


def get_ffmpeg_path() -> Optional[str]:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg and is_usable_ffmpeg(Path(system_ffmpeg)):
        return system_ffmpeg
    if imageio_ffmpeg is None:
        return None
    try:
        source = Path(imageio_ffmpeg.get_ffmpeg_exe())
        target_dir = TOOLS_DIR / "ffmpeg"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "ffmpeg.exe"
        if not target.exists() or target.stat().st_size != source.stat().st_size:
            shutil.copy2(source, target)
        return str(target)
    except Exception:
        return None


def is_usable_ffmpeg(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size < 1_000_000:
            return False
        result = subprocess.run([str(path), "-version"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0 and "ffmpeg" in (result.stdout + result.stderr).lower()
    except Exception:
        return False


def require_ffmpeg() -> str:
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("لم نجد ffmpeg. ثبّت المتطلبات أو أضف ffmpeg إلى PATH.")
    return ffmpeg


def has_module(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def run_cmd(args: list[str], error_message: str, job_id: Optional[str] = None) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    ffmpeg = get_ffmpeg_path()
    if ffmpeg:
        path_key = "Path" if "Path" in env else "PATH"
        env[path_key] = str(Path(ffmpeg).parent) + os.pathsep + env.get(path_key, "")
        env["PATH"] = env[path_key]
    result = subprocess.run(args, capture_output=True, text=True, env=env)
    
    if job_id:
        log_path = job_dir(job_id) / "cmd_log.txt"
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n--- COMMAND: {' '.join(args)} ---\n")
                f.write(f"RETURN CODE: {result.returncode}\n")
                f.write(f"STDOUT:\n{result.stdout}\n")
                f.write(f"STDERR:\n{result.stderr}\n")
        except Exception as e:
            print("Failed to save log:", e)

    if result.returncode != 0:
        details = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        if len(details) > 2000:
            details = details[-2000:]
        raise RuntimeError(f"{error_message}\n{details}")


def find_downloaded_media(workdir: Path) -> Optional[Path]:
    ignored_suffixes = {".part", ".ytdl", ".json", ".wav", ".txt"}
    candidates = [
        path
        for path in workdir.iterdir()
        if path.is_file() and path.suffix.lower() not in ignored_suffixes and not path.name.startswith(".")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if not suffix or len(suffix) > 12:
        return ".bin"
    return suffix


def start_worker(target, job_id: str) -> None:
    thread = threading.Thread(target=target, args=(job_id,), daemon=True)
    thread.start()


def acquire_processing_slot(job_id: str):
    if not processing_semaphore.acquire(blocking=False):
        with queue_lock:
            if job_id not in waiting_processing_jobs:
                waiting_processing_jobs.append(job_id)
        while True:
            update_queue_status(job_id)
            if processing_semaphore.acquire(timeout=3):
                break
    with queue_lock:
        if job_id in waiting_processing_jobs:
            waiting_processing_jobs.remove(job_id)
        active_processing_jobs.add(job_id)

    def release() -> None:
        with queue_lock:
            active_processing_jobs.discard(job_id)
        processing_semaphore.release()

    return release


def update_queue_status(job_id: str) -> None:
    with queue_lock:
        try:
            position = waiting_processing_jobs.index(job_id) + 1
        except ValueError:
            position = 1
        queue_length = len(waiting_processing_jobs)
    estimate = max(0, position * ESTIMATED_PROCESSING_SECONDS)
    update_job(
        job_id,
        status="queued",
        stage="في قائمة الانتظار",
        progress=2,
        message=f"دورك في طابور العزل رقم {position}. سنبدأ تلقائياً عندما يفرغ خادم المعالجة.",
        queue_position=position,
        queue_length=queue_length,
        estimated_wait_seconds=estimate,
    )


def clear_generated_outputs(job_id: str) -> None:
    workdir = job_dir(job_id)
    for relative in [
        "analysis.wav",
        "clean-audio.m4a",
        "purified-audio.m4a",
        "purified.mp4",
        "separated",
    ]:
        path = workdir / relative
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def cleanup_expired_jobs() -> int:
    now = time.time()
    ttl_seconds = JOB_TTL_HOURS * 3600
    active_statuses = {"queued", "downloading", "extracting", "separating", "analyzing", "purifying"}
    expired_ids: list[str] = []
    with jobs_lock:
        for job_id, job in list(jobs.items()):
            if job.get("status") in active_statuses:
                continue
            updated_at = float(job.get("updated_at") or job.get("created_at") or now)
            if now - updated_at >= ttl_seconds:
                expired_ids.append(job_id)
        for job_id in expired_ids:
            jobs.pop(job_id, None)

    for job_id in expired_ids:
        with queue_lock:
            if job_id in waiting_processing_jobs:
                waiting_processing_jobs.remove(job_id)
            active_processing_jobs.discard(job_id)
        path = job_dir(job_id).resolve()
        try:
            if path.parent == JOBS_DIR and path.exists():
                shutil.rmtree(path)
        except OSError:
            pass
    return len(expired_ids)


def cleanup_expired_jobs_loop() -> None:
    while True:
        time.sleep(CLEANUP_INTERVAL_SECONDS)
        cleanup_expired_jobs()


def start_cleanup_worker() -> None:
    thread = threading.Thread(target=cleanup_expired_jobs_loop, daemon=True)
    thread.start()


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def get_internal_job(job_id: str) -> Optional[Dict[str, Any]]:
    with jobs_lock:
        job = jobs.get(job_id)
        return dict(job) if job else None


def update_job(job_id: str, **updates: Any) -> None:
    with jobs_lock:
        if job_id not in jobs:
            return
        jobs[job_id].update(updates)
        jobs[job_id]["updated_at"] = time.time()
    persist_job(job_id)


def public_job(job_id: str) -> Optional[Dict[str, Any]]:
    job = get_internal_job(job_id)
    if not job:
        return None
    public = {
        key: value
        for key, value in job.items()
        if key
        not in {
            "confidence",
            "original_path",
            "audio_path",
            "vocals_path",
            "instrumental_path",
            "purified_path",
            "purified_audio_path",
            "clean_audio_path",
        }
    }
    public["can_download_original"] = job["status"] in {"clean", "direct"}
    public["can_purify"] = job["status"] == "needs_consent"
    public["can_download_purified"] = job["status"] == "complete"
    if public["can_download_original"]:
        public["download_url"] = f"/api/jobs/{job_id}/download?kind=original"
        public["download_urls"] = {
            "video": f"/api/jobs/{job_id}/download?kind=original",
            "audio": f"/api/jobs/{job_id}/download?kind=clean_audio" if job["status"] == "clean" and job.get("clean_audio_path") else None,
        }
    elif public["can_download_purified"]:
        public["download_url"] = f"/api/jobs/{job_id}/download?kind=purified"
        public["download_urls"] = {
            "video": f"/api/jobs/{job_id}/download?kind=purified",
            "audio": f"/api/jobs/{job_id}/download?kind=purified_audio" if job.get("purified_audio_path") else None,
        }
    else:
        public["download_url"] = None
        public["download_urls"] = {"video": None, "audio": None}
    return public


def persist_job(job_id: str) -> None:
    job = get_internal_job(job_id)
    if not job:
        return
    path = job_dir(job_id) / "job.json"
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def fail_job(job_id: str, exc: Exception) -> None:
    trace = traceback.format_exc()
    message = friendly_error(str(exc))
    update_job(
        job_id,
        status="failed",
        stage="تعذر إكمال المعالجة",
        progress=0,
        message=message,
        error=trace,
    )


def friendly_error(message: str) -> str:
    if "Requested format is not available" in message:
        return "تعذر العثور على صيغة قابلة للتحميل لهذا الرابط. حدّث yt-dlp أو جرّب رابطاً آخر."
    if "فشل محرك عزل الصوت" in message:
        if "longer segment" in message or "Maximum segment" in message:
            return "تعذر تشغيل محرك العزل بسبب إعداد داخلي غير مناسب. تم ضبطه الآن؛ اضغط إعادة المحاولة."
        if "AssertionError" in message or "pad1d" in message:
            return "تعذر عزل الصوت لأن الملف قصير جداً أو صامت تقريباً. جرّب ملفاً أطول قليلاً أو مقطعاً واضح الصوت."
        if "Killed" in message or "out of memory" in message.lower() or "cannot allocate memory" in message.lower():
            return "موارد الاستضافة لم تكفِ لعزل هذا المقطع. جرّب مقطعاً أقصر."
        return "تعذر تشغيل محرك عزل الصوت على هذا الملف. جرّب ملفاً أقصر أو صيغة صوت/فيديو أخرى."
    if "بقي أثر موسيقي واضح" in message:
        return "بقي أثر موسيقي واضح بعد التنقية، لذلك أوقفنا إخراج الملف احتياطاً. جرّب مقطعاً آخر أو ارفع نسخة أقصر."
    if "HTTP Error 403" in message or "Forbidden" in message:
        return "منع YouTube تنزيل هذا الرابط مؤقتاً. أعد المحاولة، وإن تكرر الخطأ فقد يحتاج الرابط إلى كوكيز المتصفح أو طريقة تحميل مختلفة."
    if "UNEXPECTED_EOF_WHILE_READING" in message or "SSL" in message:
        return "تعذر تنزيل الرابط من YouTube بسبب انقطاع اتصال الاستضافة. جرّب مرة أخرى أو ارفع الملف من جهازك عبر تبويب ملف."
    if "Sign in to confirm" in message or "not a bot" in message or "cookies" in message.lower():
        if YTDLP_PROXY:
            return "هذا الرابط يحتاج جلسة YouTube أقوى أو بروكسي أنظف قبل أن يستطيع الخادم تحميله."
        return "هذا الرابط يحتاج جلسة YouTube وبروكسي نظيفاً قبل أن يستطيع الخادم تحميله."
    return message


def load_persisted_jobs() -> None:
    active_statuses = {"queued", "downloading", "extracting", "separating", "analyzing", "purifying"}
    for path in JOBS_DIR.glob("*/job.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data.get("id"):
                continue
            data.setdefault("purified_audio_path", None)
            data.setdefault("clean_audio_path", None)
            data.setdefault("duration_seconds", None)
            data.setdefault("residual_music_ratio", None)
            data.setdefault("purification_mode", None)
            data.setdefault("queue_position", 0)
            data.setdefault("queue_length", 0)
            data.setdefault("estimated_wait_seconds", 0)
            if data.get("status") in active_statuses:
                data["status"] = "failed"
                data["stage"] = "توقفت المعالجة"
                data["progress"] = 0
                data["message"] = "توقف الخادم أثناء العمل. اضغط إعادة المحاولة ليكمل من الملف المحفوظ إن كان موجوداً."
                data["error"] = "Server restarted while job was active."
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            with jobs_lock:
                jobs[data["id"]] = data
        except Exception:
            continue


load_persisted_jobs()
cleanup_expired_jobs()
start_cleanup_worker()
