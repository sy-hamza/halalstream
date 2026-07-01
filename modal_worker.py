from __future__ import annotations

import base64
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import modal


MODAL_GPU = os.getenv("HALALSTREAM_MODAL_GPU", "T4")
DEMUCS_MODEL = os.getenv("HALALSTREAM_DEMUCS_MODEL", "htdemucs")
DEMUCS_SEGMENT = int(float(os.getenv("HALALSTREAM_DEMUCS_SEGMENT", "7")))
DEMUCS_MAX_SEGMENT = int(float(os.getenv("HALALSTREAM_DEMUCS_MAX_SEGMENT", "7")))
DEMUCS_OVERLAP = float(os.getenv("HALALSTREAM_DEMUCS_OVERLAP", "0.75"))
DEMUCS_SHIFTS = max(1, int(os.getenv("HALALSTREAM_DEMUCS_SHIFTS", "4")))
MUSIC_RATIO_THRESHOLD = float(os.getenv("HALALSTREAM_MUSIC_RATIO_THRESHOLD", "0.04"))
RESIDUAL_MUSIC_RATIO_THRESHOLD = float(os.getenv("HALALSTREAM_RESIDUAL_MUSIC_RATIO_THRESHOLD", "0.12"))
RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD = float(os.getenv("HALALSTREAM_RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD", "0.01"))

image = (
    modal.Image.from_registry(
        "pytorch/pytorch:2.2.2-cuda12.1-cudnn8-runtime",
        add_python="3.10",
    )
    .apt_install("ffmpeg")
    .pip_install(
        "fastapi[standard]==0.115.6",
        "python-multipart==0.0.20",
        "demucs==4.0.1",
        "numpy==1.26.4",
        "soundfile==0.13.1",
    )
)

app = modal.App("halalstream-purifier")
cache_volume = modal.Volume.from_name("halalstream-demucs-cache", create_if_missing=True)


@app.function(
    image=image,
    gpu=MODAL_GPU,
    timeout=1800,
    scaledown_window=60,
    volumes={"/root/.cache": cache_volume},
    secrets=[modal.Secret.from_name("halalstream-modal-secret")],
)
@modal.fastapi_endpoint(method="POST")
async def purify(request):
    from fastapi import HTTPException

    secret = os.getenv("HALALSTREAM_MODAL_SECRET", "")
    if not secret or request.headers.get("x-halalstream-secret") != secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=400, detail="Missing file")

    source_name = str(form.get("filename") or getattr(upload, "filename", "source.bin"))
    suffix = Path(source_name).suffix or ".bin"
    with tempfile.TemporaryDirectory() as tmp_dir:
        workdir = Path(tmp_dir)
        source = workdir / f"source{suffix}"
        with source.open("wb") as target:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)

        audio = extract_audio(workdir, source)
        vocals, instrumental = separate_vocals(workdir, audio)
        ratio, confidence = estimate_music_ratio(audio, vocals, instrumental)
        has_music = ratio >= MUSIC_RATIO_THRESHOLD

        if not has_music:
            clean_audio = encode_audio(workdir, audio, "clean-audio.m4a", filter_vocals=False)
            return {
                "status": "clean",
                "has_music": False,
                "instrumental_ratio": round(ratio, 4),
                "confidence": round(confidence, 4),
                "residual_music_ratio": None,
                "purification_mode": None,
                "message": "الحمد لله، لم يظهر مؤشر معتبر للمعازف.",
                "audio_base64": base64.b64encode(clean_audio.read_bytes()).decode("ascii"),
            }

        result = purify_with_retries(workdir, vocals, instrumental)
        purified_audio = encode_audio(workdir, result["path"], "purified-audio.m4a", filter_vocals=True)
        return {
            "status": "complete",
            "has_music": True,
            "instrumental_ratio": round(ratio, 4),
            "confidence": round(confidence, 4),
            "residual_music_ratio": round(float(result["ratio"]), 4),
            "purification_mode": result["mode"],
            "message": completion_message(result),
            "audio_base64": base64.b64encode(purified_audio.read_bytes()).decode("ascii"),
        }


def run_cmd(args: list[str], error_message: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(args, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        details = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        raise RuntimeError(f"{error_message}\n{details[-3000:]}")


def extract_audio(workdir: Path, source: Path) -> Path:
    audio = workdir / "analysis.wav"
    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-sample_fmt",
            "s16",
            str(audio),
        ],
        "Failed to extract audio.",
    )
    return audio


def separate_vocals(workdir: Path, audio: Path) -> tuple[Path, Path]:
    import torch

    out_dir = workdir / "separated"
    segment = max(1, min(DEMUCS_SEGMENT, DEMUCS_MAX_SEGMENT))
    command = [
        sys.executable,
        "-m",
        "demucs.separate",
        "--two-stems",
        "vocals",
        "-n",
        DEMUCS_MODEL,
        "-j",
        "1",
        "--shifts",
        str(DEMUCS_SHIFTS),
        "--overlap",
        str(DEMUCS_OVERLAP),
        "--segment",
        str(segment),
    ]
    if torch.cuda.is_available():
        command.extend(["-d", "cuda"])
    command.extend(["-o", str(out_dir), str(audio)])
    run_cmd(command, "Failed to separate vocals.")
    root = out_dir / DEMUCS_MODEL / audio.stem
    vocals = root / "vocals.wav"
    instrumental = root / "no_vocals.wav"
    if not vocals.exists() or not instrumental.exists():
        matches_v = list(out_dir.glob("**/vocals.wav"))
        matches_i = list(out_dir.glob("**/no_vocals.wav"))
        if matches_v and matches_i:
            return matches_v[0], matches_i[0]
        raise RuntimeError("Demucs finished without expected stems.")
    return vocals, instrumental


def encode_audio(workdir: Path, source_audio: Path, filename: str, filter_vocals: bool) -> Path:
    out = workdir / filename
    cmd = ["ffmpeg", "-y", "-i", str(source_audio), "-vn"]
    if filter_vocals:
        cmd.extend(["-af", "highpass=f=70,lowpass=f=9500"])
    cmd.extend(["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    run_cmd(cmd, "Failed to encode audio.")
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
    import wave

    with wave.open(str(path), "rb") as wav:
        width = wav.getsampwidth()
        total_square = 0.0
        total_samples = 0
        while True:
            data = wav.readframes(44100 * 6)
            if not data:
                break
            rms = audioop.rms(data, width)
            samples = len(data) / max(width, 1)
            total_square += (rms * rms) * samples
            total_samples += samples
    return math.sqrt(total_square / total_samples) if total_samples else 0.0


def purify_with_retries(workdir: Path, vocals_path: Path, instrumental_path: Path) -> Dict[str, Any]:
    best: Optional[Dict[str, Any]] = None
    for mode in ["balanced", "strong", "extreme"]:
        candidate = purify_vocal_stem(vocals_path, instrumental_path, workdir, mode)
        ratio, absolute = estimate_residual_music_bleed(candidate, instrumental_path)
        result = {
            "path": candidate,
            "mode": mode,
            "ratio": ratio,
            "absolute": absolute,
            "safe": ratio < RESIDUAL_MUSIC_RATIO_THRESHOLD or absolute < RESIDUAL_MUSIC_ABSOLUTE_THRESHOLD,
        }
        if best is None or ratio < best["ratio"]:
            best = result
        if result["safe"]:
            return result
    assert best is not None
    return best


def completion_message(result: Dict[str, Any]) -> str:
    if result.get("safe"):
        return "تم بحمد الله إعداد نسخة منقّاة عبر عامل Modal. الملف جاهز للتحميل."
    return "اكتملت أقوى محاولة تنقية متاحة عبر Modal. قد يتأثر الصوت البشري، لكننا أعدنا المحاولة لتقليل بقايا المعازف قدر الإمكان."


def purification_profile(mode: str) -> Dict[str, Any]:
    profiles = {
        "balanced": (0.55, 0.75, 0.65, 0.55, 0.015, 0.16, 0.16, 0.10, 0.08, 0.28, 0.20, 0.80),
        "strong": (0.85, 1.05, 0.90, 0.75, 0.004, 0.08, 0.22, 0.16, 0.04, 0.38, 0.32, 0.65),
        "extreme": (1.15, 1.35, 1.10, 0.90, 0.001, 0.035, 0.30, 0.25, 0.015, 0.48, 0.45, 0.45),
    }
    keys = [
        "subtract_base", "subtract_bin", "subtract_frame", "subtract_share",
        "floor_base", "floor_voice", "dominant_share", "dominant_voice",
        "dominant_cap", "confident_share", "confident_voice", "confident_floor",
    ]
    return dict(zip(keys, profiles.get(mode, profiles["balanced"])))


def purify_vocal_stem(vocals_path: Path, instrumental_path: Path, workdir: Path, mode: str) -> Path:
    import numpy as np
    import soundfile as sf

    profile = purification_profile(mode)
    vocals, sr = sf.read(str(vocals_path), always_2d=True, dtype="float32")
    instrumental, inst_sr = sf.read(str(instrumental_path), always_2d=True, dtype="float32")
    if sr != inst_sr:
        raise RuntimeError("Stem sample rates do not match.")
    length = min(len(vocals), len(instrumental))
    vocals = vocals[:length]
    instrumental = match_audio_channels(instrumental[:length], vocals.shape[1])
    chunk_len = max(sr * 5, int(sr * 30))
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
                profile,
            )
        weight = chunk_weight(end - start, overlap_len, start > 0, end < length)
        purified[start:end] += chunk_out * weight
        weights[start:end] += weight
        if end >= length:
            break
        start += step
    purified = soft_limit_audio(purified / np.maximum(weights, 1e-6))
    out = workdir / f"vocals_purified_{mode}.wav"
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


def suppress_music_leakage(vocal_chunk, instrumental_chunk, profile: Dict[str, Any]):
    import numpy as np

    n_fft = 2048
    hop = 512
    eps = 1e-8
    vocal_spec, total_len, pad = stft_np(vocal_chunk, n_fft, hop)
    instrumental_spec, _, _ = stft_np(instrumental_chunk, n_fft, hop)
    vocal_mag = np.abs(vocal_spec).astype(np.float32)
    instrumental_mag = np.abs(instrumental_spec).astype(np.float32)
    vocal_frame = np.sqrt(np.mean(vocal_mag * vocal_mag, axis=1) + eps)
    instrumental_frame = np.sqrt(np.mean(instrumental_mag * instrumental_mag, axis=1) + eps)
    instrumental_share = instrumental_frame / (vocal_frame + instrumental_frame + eps)
    voice_share = vocal_frame / (vocal_frame + instrumental_frame + eps)
    bin_share = instrumental_mag / (vocal_mag + instrumental_mag + eps)
    subtract_scale = (
        profile["subtract_base"] + profile["subtract_bin"] * bin_share
    ) * (
        profile["subtract_frame"] + profile["subtract_share"] * instrumental_share[:, None]
    )
    floor = profile["floor_base"] + profile["floor_voice"] * voice_share[:, None] * (1.0 - bin_share)
    cleaned_mag = np.maximum(vocal_mag - instrumental_mag * subtract_scale, vocal_mag * floor)
    phase = vocal_spec / (vocal_mag + eps)
    cleaned = istft_np(cleaned_mag * phase, total_len, pad, len(vocal_chunk), n_fft, hop)
    cleaned *= interpolate_frame_gain(voice_activity_gain(vocal_frame, instrumental_frame, profile), len(cleaned), hop)
    return np.clip(cleaned, -1.0, 1.0).astype(np.float32)


def stft_np(signal, n_fft: int, hop: int):
    import numpy as np

    signal = np.asarray(signal, dtype=np.float32)
    pad = n_fft // 2
    padded = np.pad(signal, (pad, pad), mode="reflect" if len(signal) > pad else "constant")
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
    dominant = (share < profile["dominant_share"]) & (absolute_voice < profile["dominant_voice"])
    gain[dominant] = np.minimum(gain[dominant], profile["dominant_cap"])
    confident = (share > profile["confident_share"]) | (absolute_voice > profile["confident_voice"])
    gain[confident] = np.maximum(gain[confident], profile["confident_floor"])
    gain = max_filter_1d(np.clip(gain, 0.0, 1.0), 3)
    return smooth_1d(gain, 6).astype(np.float32)


def interpolate_frame_gain(frame_gain, length: int, hop: int):
    import numpy as np

    if len(frame_gain) == 1:
        return np.full(length, frame_gain[0], dtype=np.float32)
    return np.interp(
        np.arange(length, dtype=np.float32),
        np.arange(len(frame_gain), dtype=np.float32) * hop,
        frame_gain,
        left=frame_gain[0],
        right=frame_gain[-1],
    ).astype(np.float32)


def max_filter_1d(values, radius: int):
    import numpy as np

    padded = np.pad(values, (radius, radius), mode="edge")
    return np.max(np.vstack([padded[offset:offset + len(values)] for offset in range(radius * 2 + 1)]), axis=0)


def smooth_1d(values, radius: int):
    import numpy as np

    kernel = np.ones(radius * 2 + 1, dtype=np.float32)
    kernel /= np.sum(kernel)
    return np.convolve(np.pad(values, (radius, radius), mode="edge"), kernel, mode="valid")


def soft_limit_audio(audio):
    import numpy as np

    audio = np.nan_to_num(audio.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.98:
        audio *= 0.98 / peak
    return np.clip(audio, -0.98, 0.98).astype(np.float32)


def estimate_residual_music_bleed(purified_vocals: Path, instrumental_path: Path) -> tuple[float, float]:
    import numpy as np
    import soundfile as sf

    clean, sr = sf.read(str(purified_vocals), always_2d=True, dtype="float32")
    instrumental, inst_sr = sf.read(str(instrumental_path), always_2d=True, dtype="float32")
    if sr != inst_sr:
        return 0.0, 0.0
    length = min(len(clean), len(instrumental))
    clean = np.mean(clean[:length], axis=1).astype(np.float32)
    instrumental = np.mean(instrumental[:length], axis=1).astype(np.float32)
    clean_spec, _, _ = stft_np(clean, 2048, 512)
    inst_spec, _, _ = stft_np(instrumental, 2048, 512)
    clean_mag = np.abs(clean_spec).astype(np.float32)
    inst_mag = np.abs(inst_spec).astype(np.float32)
    ratio = float(np.sum(np.minimum(clean_mag, inst_mag)) / max(np.sum(clean_mag), 1e-8))
    absolute = float(np.sqrt(np.mean(np.square(clean))))
    return ratio, absolute


if __name__ == "__main__":
    print("Deploy with: modal deploy modal_worker.py")
