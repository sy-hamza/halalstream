from __future__ import annotations

import json
import math
import os
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
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
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


ROOT = Path(__file__).resolve().parent
STORAGE = Path(os.getenv("HALALSTREAM_STORAGE_DIR", ROOT / "storage")).resolve()
JOBS_DIR = STORAGE / "jobs"
TOOLS_DIR = ROOT / "tools"
ASSETS_DIR = ROOT / "assets"
MUSIC_RATIO_THRESHOLD = 0.13
DEMUCS_MODEL = os.getenv("HALALSTREAM_DEMUCS_MODEL", "htdemucs")
DEMUCS_JOBS = int(os.getenv("HALALSTREAM_DEMUCS_JOBS", "1"))
DEMUCS_SEGMENT = int(float(os.getenv("HALALSTREAM_DEMUCS_SEGMENT", "3")))
DEMUCS_OVERLAP = float(os.getenv("HALALSTREAM_DEMUCS_OVERLAP", "0.1"))
MAX_ACTIVE_PROCESSING_JOBS = max(1, int(os.getenv("HALALSTREAM_MAX_ACTIVE_PROCESSING_JOBS", "1")))
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

COBALT_FALLBACK_APIS = [
    "https://api.cobalt.blackcat.sweeux.org/",
    "https://api.qwkuns.me/",
    "https://cobaltapi.kittycat.boo/",
    "https://cobaltapi.squair.xyz/",
    "https://dog.kittycat.boo/",
    "https://fox.kittycat.boo/",
    "https://grapefruit.clxxped.lol/",
    "https://lime.clxxped.lol/",
    "https://melon.clxxped.lol/",
    "https://nuko-c.meowing.de/",
    "https://subito-c.meowing.de/",
    "https://cobalt.alpha.wolfy.love/",
    "https://cobalt.omega.wolfy.love/",
    "https://blossom.imput.net/",
    "https://sunny.imput.net/",
]

ASSETS_DIR.mkdir(exist_ok=True)
STORAGE.mkdir(exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="HalalStream Server", version="0.3.0")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()
processing_semaphore = threading.BoundedSemaphore(MAX_ACTIVE_PROCESSING_JOBS)


class LinkJobRequest(BaseModel):
    url: HttpUrl
    purify_mode: str = "purify"
    quality: str = "high"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/app.js")
def app_js() -> FileResponse:
    return FileResponse(ROOT / "app.js", media_type="application/javascript")


@app.get("/styles.css")
def styles() -> FileResponse:
    return FileResponse(ROOT / "styles.css", media_type="text/css")


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "python": sys.version.split()[0],
        "ffmpeg": bool(get_ffmpeg_path()),
        "yt_dlp": yt_dlp is not None,
        "demucs": has_module("demucs"),
        "demucs_model": DEMUCS_MODEL,
        "demucs_jobs": DEMUCS_JOBS,
        "demucs_segment": DEMUCS_SEGMENT,
        "demucs_overlap": DEMUCS_OVERLAP,
        "max_active_processing_jobs": MAX_ACTIVE_PROCESSING_JOBS,
        "hosted_space": HOSTED_SPACE,
        "link_downloads_reliable": LINK_DOWNLOADS_RELIABLE,
        "yt_dlp_proxy": bool(YTDLP_PROXY),
        "youtube_pot_provider": bool(YOUTUBE_POT_BASE_URL),
        "youtube_fetch_pot": YOUTUBE_FETCH_POT,
        "youtube_remote_components": list(YOUTUBE_REMOTE_COMPONENTS),
        "message": "الخادم يعمل. اكتمال المعالجة يحتاج yt-dlp و ffmpeg و demucs.",
    }


@app.post("/api/jobs/link")
def create_link_job(payload: LinkJobRequest) -> Dict[str, str]:
    if is_youtube_url(str(payload.url)) and not LINK_DOWNLOADS_RELIABLE:
        raise HTTPException(
            status_code=400,
            detail="الاستضافة المجانية الحالية لا تنزّل روابط YouTube بثبات. نزّل الملف على جهازك ثم ارفعه من تبويب ملف، أو انقل الخادم إلى VPS أقوى.",
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

    with original_path.open("wb") as target:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)

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

    update_job(job_id, status="purifying", stage="إزالة المعازف", progress=82, message="تمت الموافقة. نجهز نسخة منقّاة الآن.")
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
        if job["status"] != "clean":
            raise HTTPException(status_code=403, detail="لا يسمح بتحميل الملف الأصلي إلا بعد ثبوت خلوه من المعازف.")
        path = job.get("original_path")
        filename = f"halalstream-clean-{job_id}{Path(path).suffix if path else '.mp4'}"
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
        "original_path": None,
        "audio_path": None,
        "vocals_path": None,
        "instrumental_path": None,
        "purified_path": None,
        "purified_audio_path": None,
        "clean_audio_path": None,
        "error": None,
    }
    with jobs_lock:
        jobs[job_id] = job
    persist_job(job_id)
    return job


def process_job(job_id: str) -> None:
    release_slot = acquire_processing_slot(job_id)
    try:
        ensure_runtime()
        job = get_internal_job(job_id)
        if not job:
            return

        if job["source_type"] == "link":
            existing_original = job.get("original_path")
            if existing_original and Path(existing_original).exists():
                original = Path(existing_original)
                update_job(job_id, status="extracting", stage="استخراج الصوت", progress=24, message="نستخدم الملف الموجود ونستخرج الصوت من جديد.")
            else:
                original = download_link(job_id, job["source_url"])
        else:
            original = Path(job["original_path"])
            update_job(job_id, status="extracting", stage="استخراج الصوت", progress=24, message="نجهز الملف للتحليل.")

        purify_mode = job.get("purify_mode", "purify")
        quality = job.get("quality", "high")

        if purify_mode == "direct":
            # Direct bypass mode: mark clean instantly and return original path as clean path
            update_job(
                job_id,
                status="clean",
                stage="جاهز للتحميل",
                progress=100,
                message="الحمد لله، تم التجهيز للتحميل المباشر فوراً بناءً على تأكيدك بخلو المقطع من أي معازف.",
                has_music=False,
                instrumental_ratio=0.0,
                confidence=1.0,
                clean_audio_path=str(original),
            )
            return

        audio = extract_audio(job_id, original)
        vocals, instrumental = separate_vocals(job_id, audio, quality=quality)
        ratio, confidence = estimate_music_ratio(audio, vocals, instrumental)
        has_music = ratio >= MUSIC_RATIO_THRESHOLD

        if has_music:
            update_job(
                job_id,
                status="needs_consent",
                stage="الحكم الشرعي قبل التحميل",
                progress=78,
                message="تم رصد مسار معازف. أوقفنا التحميل حتى توافق على إزالته.",
                has_music=True,
                instrumental_ratio=round(ratio, 4),
                confidence=round(confidence, 4),
                vocals_path=str(vocals),
                instrumental_path=str(instrumental),
            )
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
        release_slot()


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

        ffmpeg = require_ffmpeg()
        audio_out = encode_audio(job_id, vocals, "purified-audio.m4a", 86, "نجهز نسخة صوتية منقّاة وسريعة التحميل.", filter_vocals=True)
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
        )
        update_job(
            job_id,
            status="complete",
            stage="تمت إزالة المعازف",
            progress=100,
            message="تم بحمد الله عزل مسار المعازف وإعداد نسخة منقّاة قدر الإمكان. الملف جاهز للتحميل.",
            purified_path=str(out),
            purified_audio_path=str(audio_out),
        )
    except Exception as exc:
        fail_job(job_id, exc)


def download_via_cobalt(job_id: str, url: str, workdir: Path) -> Path:
    context = ssl._create_unverified_context()
    payload = {
        "url": url,
        "audioFormat": "mp3",
        "downloadMode": "audio",
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    
    for api_url in COBALT_FALLBACK_APIS:
        update_job(job_id, message=f"نحاول التنزيل عبر خادم مساند: {api_url.split('//')[1].split('/')[0]}")
        try:
            req = urllib.request.Request(
                api_url,
                data=data_bytes,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, context=context, timeout=15) as response:
                res = json.loads(response.read().decode("utf-8"))
                
                status = res.get("status")
                if status in ("tunnel", "redirect"):
                    download_url = res.get("url")
                    filename = res.get("filename") or "downloaded.mp3"
                    suffix = safe_suffix(filename)
                    out_path = workdir / f"downloaded{suffix}"
                    
                    update_job(job_id, message="نجح خادم التنزيل المساند. نسحب الملف الصوتي الآن...")
                    
                    dl_req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(dl_req, context=context, timeout=30) as dl_res:
                        with out_path.open("wb") as f:
                            while True:
                                chunk = dl_res.read(1024 * 1024)
                                if not chunk:
                                    break
                                f.write(chunk)
                    if out_path.exists() and out_path.stat().st_size > 0:
                        return out_path
                elif status == "picker":
                    picker_items = res.get("picker", [])
                    if picker_items:
                        download_url = picker_items[0].get("url")
                        filename = picker_items[0].get("filename") or "downloaded.mp3"
                        suffix = safe_suffix(filename)
                        out_path = workdir / f"downloaded{suffix}"
                        
                        update_job(job_id, message="نجح خادم التنزيل المساند (عبر قائمة الخيارات). نسحب الملف...")
                        
                        dl_req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(dl_req, context=context, timeout=30) as dl_res:
                            with out_path.open("wb") as f:
                                while True:
                                    chunk = dl_res.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    f.write(chunk)
                        if out_path.exists() and out_path.stat().st_size > 0:
                            return out_path
        except Exception as e:
            print(f"Cobalt download failed on {api_url}: {e}")
            
    raise RuntimeError("فشلت جميع محاولات التنزيل المباشرة وعبر الخوادم المساندة.")


def download_link(job_id: str, url: str) -> Path:
    workdir = job_dir(job_id)
    update_job(job_id, status="downloading", stage="تحميل المقطع", progress=8, message="نحمّل المقطع إلى خادم المعالجة.")
    download_errors: list[str] = []
    info: Optional[Dict[str, Any]] = None

    is_yt = is_youtube_url(url)

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
            update_job(job_id, message="تعذر التنزيل المباشر. نحاول التنزيل عبر الخوادم المساندة...")
        else:
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
            "حدّثنا الخادم ليجرب عدة قنوات تلقائياً، لكن إن تكرر الخطأ فغالباً أن الاستضافة المجانية تقطع اتصال YouTube مؤقتاً. "
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
    model = DEMUCS_MODEL # "htdemucs"
    if quality == "fast":
        model = "hdemucs_mmi"
        
    msg = "نعزل الصوت البشري عن مسار المعازف. يمكنك ترك الصفحة والرجوع لاحقاً."
    if quality == "high":
        msg += " (تنبيه: اخترت جودة فائقة؛ إن كان المقطع طويلاً فقد تستغرق المعالجة حتى 15 دقيقة على الخادم المجاني)."
        
    update_job(job_id, status="separating", stage="عزل الصوت", progress=42, message=msg)
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
        "--overlap",
        str(max(0.0, DEMUCS_OVERLAP)),
        "-o",
        str(out_dir),
        str(audio),
    ]
    if DEMUCS_SEGMENT > 0:
        command.extend(["--segment", str(int(DEMUCS_SEGMENT))])
    run_cmd(command, "فشل محرك عزل الصوت.")

    vocals, instrumental = find_demucs_stems(out_dir, audio.stem)
    if not vocals.exists() or not instrumental.exists():
        raise RuntimeError("انتهى محرك العزل لكن ملفات الصوت المتوقعة غير موجودة.")

    update_job(job_id, status="analyzing", stage="تحليل النتيجة", progress=68, message="نقيس أثر الطبقة غير الصوتية قبل إصدار الحكم.")
    return vocals, instrumental


def find_demucs_stems(out_dir: Path, stem_name: str) -> tuple[Path, Path]:
    expected_root = out_dir / DEMUCS_MODEL / stem_name
    vocals = expected_root / "vocals.wav"
    instrumental = expected_root / "no_vocals.wav"
    if vocals.exists() and instrumental.exists():
        return vocals, instrumental

    vocals_matches = list(out_dir.glob(f"**/{stem_name}/vocals.wav")) + list(out_dir.glob("**/vocals.wav"))
    instrumental_matches = list(out_dir.glob(f"**/{stem_name}/no_vocals.wav")) + list(out_dir.glob("**/no_vocals.wav"))
    if vocals_matches and instrumental_matches:
        return vocals_matches[0], instrumental_matches[0]

    return vocals, instrumental


def encode_audio(job_id: str, source_audio: Path, filename: str, progress: int, message: str, filter_vocals: bool = False) -> Path:
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
    if filter_vocals:
        # Highpass filter at 100Hz to remove low-end music/bass leakage
        # Audio gate to completely silence background noise/music during pauses in speech/singing
        cmd.extend(["-af", "highpass=f=100,agate=threshold=0.015:range=0.05:attack=50:release=300"])
        
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
    )
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("فشل تجهيز ملف الصوت: لم ينتج ffmpeg ملفاً صالحاً.")
    return out


def estimate_music_ratio(audio: Path, vocals: Path, instrumental: Path) -> tuple[float, float]:
    total = rms_wav(audio)
    vocal_rms = rms_wav(vocals)
    instrumental_rms = rms_wav(instrumental)
    denominator = max(vocal_rms + instrumental_rms, total, 1.0)
    ratio = instrumental_rms / denominator
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


def run_cmd(args: list[str], error_message: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    ffmpeg = get_ffmpeg_path()
    if ffmpeg:
        path_key = "Path" if "Path" in env else "PATH"
        env[path_key] = str(Path(ffmpeg).parent) + os.pathsep + env.get(path_key, "")
        env["PATH"] = env[path_key]
    result = subprocess.run(args, capture_output=True, text=True, env=env)
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
        update_job(
            job_id,
            status="queued",
            stage="في قائمة الانتظار",
            progress=2,
            message="الطلب محفوظ في قائمة الانتظار. سنبدأ المعالجة تلقائياً عندما يفرغ الخادم.",
        )
        processing_semaphore.acquire()

    def release() -> None:
        processing_semaphore.release()

    return release


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
    public["can_download_original"] = job["status"] == "clean"
    public["can_purify"] = job["status"] == "needs_consent"
    public["can_download_purified"] = job["status"] == "complete"
    if public["can_download_original"]:
        public["download_url"] = f"/api/jobs/{job_id}/download?kind=original"
        public["download_urls"] = {
            "video": f"/api/jobs/{job_id}/download?kind=original",
            "audio": f"/api/jobs/{job_id}/download?kind=clean_audio" if job.get("clean_audio_path") else None,
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
        if "AssertionError" in message or "pad1d" in message:
            return "تعذر عزل الصوت لأن الملف قصير جداً أو صامت تقريباً. جرّب ملفاً أطول قليلاً أو مقطعاً واضح الصوت."
        if "Killed" in message or "out of memory" in message.lower() or "cannot allocate memory" in message.lower():
            return "موارد الاستضافة المجانية لم تكفِ لعزل هذا المقطع. جرّب مقطعاً أقصر أو انقل الخادم إلى VPS أقوى."
        return "تعذر تشغيل محرك عزل الصوت على هذا الملف. جرّب ملفاً أقصر أو صيغة صوت/فيديو أخرى."
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
