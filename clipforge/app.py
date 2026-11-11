"""Local-first ClipForge web application and bounded background worker."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from clipforge import __version__
from clipforge.ai import ai_capabilities, select_ai_highlights, transcribe
from clipforge.captions import write_captions
from clipforge.engine import (
    audio_energy,
    download_video,
    probe,
    render_clip,
    render_thumbnail,
    select_highlights,
    validate_url,
)
from clipforge.publishing import platform_status, publish

DATA_DIR = Path(os.environ.get("CLIPFORGE_DATA_DIR", "./clipforge-data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = 2 * 1024 * 1024 * 1024
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge")
PUBLISH_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-publish")
LOCK = threading.RLock()
STATIC = Path(__file__).resolve().parent / "static"
LOGGER = logging.getLogger("uvicorn.error")

app = FastAPI(title="ClipForge", version=__version__, docs_url="/api/docs")
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


class Options(BaseModel):
    clip_seconds: int = Field(default=30, ge=10, le=120)
    max_clips: int = Field(default=25, ge=1, le=100)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    selection_mode: Literal["ai", "energy"] = "energy"
    caption_style: Literal["none", "classic", "bold", "neon"] = "none"
    effect: Literal["none", "pop", "cinema", "noir"] = "none"
    framing: Literal["pad", "crop"] = "pad"


class UrlRequest(Options):
    url: str
    rights_confirmed: bool = False


class PublishRequest(BaseModel):
    filename: str = Field(min_length=8, max_length=80)
    platform: Literal["youtube", "instagram", "facebook", "tiktok"]
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2200)
    visibility: Literal["private", "unlisted", "public"] = "private"
    approved: bool = False


def job_folder(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "Unknown job")
    folder = DATA_DIR / job_id
    if not folder.is_dir():
        raise HTTPException(404, "Unknown job")
    return folder


def _read(folder: Path) -> dict:
    with LOCK:
        return json.loads((folder / "job.json").read_text(encoding="utf-8"))


def _write(folder: Path, data: dict) -> None:
    with LOCK:
        tmp = folder / "job.json.tmp"
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, folder / "job.json")


def _patch(folder: Path, **changes) -> None:
    with LOCK:
        data = _read(folder)
        data.update(changes)
        _write(folder, data)


def _create(kind: str, options: Options, label: str) -> tuple[str, Path]:
    job_id = uuid.uuid4().hex
    folder = DATA_DIR / job_id
    folder.mkdir(mode=0o700)
    _write(folder, {
        "id": job_id,
        "source": kind,
        "label": label[:200],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "queued",
        "progress": 0,
        "error": None,
        "duration": None,
        "clips": [],
        "publications": [],
        "options": options.model_dump(),
    })
    return job_id, folder


def _process(folder: Path, source_path: Path | None = None, url: str | None = None) -> None:
    try:
        LOGGER.info("ClipForge job %s started", folder.name)
        if url:
            _patch(folder, status="downloading", progress=3)
            source_path = download_video(url, folder)
        assert source_path is not None
        _patch(folder, status="analyzing", progress=10)
        info = probe(source_path)
        job = _read(folder)
        opts = Options(**job["options"])
        energy = []
        if info["has_audio"]:
            try:
                energy = audio_energy(source_path, info["duration"])
            except (OSError, RuntimeError):
                pass
        transcript = []
        if opts.selection_mode == "ai" or opts.caption_style != "none":
            if not info["has_audio"]:
                raise RuntimeError("Speech transcription needs a video with audio")
            _patch(folder, status="transcribing", progress=12)
            LOGGER.info("ClipForge job %s transcribing audio", folder.name)
            transcript = transcribe(source_path, DATA_DIR / "models")
            (folder / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
        if opts.selection_mode == "ai":
            _patch(folder, status="selecting", progress=16)
            candidates = select_ai_highlights(
                transcript, info["duration"], opts.clip_seconds, opts.max_clips, energy,
            )
        else:
            candidates = select_highlights(
                info["duration"], opts.clip_seconds, opts.max_clips, energy,
            )
        if not candidates:
            raise ValueError("No suitable clips were found")
        output = folder / "clips"
        output.mkdir(exist_ok=True)
        _patch(folder, status="rendering", duration=round(info["duration"], 2), progress=20)
        LOGGER.info("ClipForge job %s rendering %s clips", folder.name, len(candidates))
        clips = []
        for index, candidate in enumerate(candidates, 1):
            filename = f"clip-{index:03d}.mp4"
            thumbnail = f"clip-{index:03d}.jpg"
            subtitles = None
            if opts.caption_style != "none":
                candidate_ass = output / f"clip-{index:03d}.ass"
                if write_captions(transcript, candidate["start"], candidate["duration"],
                                  opts.caption_style, candidate_ass):
                    subtitles = candidate_ass
            render_clip(
                source_path, output / filename, candidate["start"], candidate["duration"],
                opts.aspect_ratio, captions=subtitles, effect=opts.effect, framing=opts.framing,
            )
            try:
                render_thumbnail(output / filename, output / thumbnail)
            except (OSError, RuntimeError):
                thumbnail = None
            clips.append({
                "filename": filename, "thumbnail": thumbnail, "number": index,
                "captions_added": subtitles is not None, "effect": opts.effect,
                "framing": opts.framing, **candidate,
            })
            _patch(folder, clips=clips, progress=20 + int(index / len(candidates) * 79))
        _patch(folder, status="completed", progress=100)
        LOGGER.info("ClipForge job %s completed", folder.name)
    except Exception as exc:
        LOGGER.exception("ClipForge job %s failed", folder.name)
        _patch(folder, status="failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/api/ai/status")
def get_ai_status():
    return ai_capabilities()


@app.get("/api/publishing/platforms")
def get_platforms():
    return platform_status()


@app.post("/api/jobs", status_code=202)
def submit_url(body: UrlRequest):
    if not body.rights_confirmed:
        raise HTTPException(400, "Confirm you own or have permission to clip this video")
    try:
        url = validate_url(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    job_id, folder = _create("url", body, url)
    POOL.submit(_process, folder, None, url)
    return {"id": job_id, "status": "queued"}


@app.post("/api/uploads", status_code=202)
async def submit_upload(
    file: UploadFile = File(...),
    clip_seconds: int = Form(30),
    max_clips: int = Form(25),
    aspect_ratio: str = Form("9:16"),
    selection_mode: str = Form("energy"),
    caption_style: str = Form("none"),
    effect: str = Form("none"),
    framing: str = Form("pad"),
    rights_confirmed: bool = Form(False),
):
    if not rights_confirmed:
        raise HTTPException(400, "Confirm you own or have permission to clip this video")
    try:
        opts = Options(
            clip_seconds=clip_seconds, max_clips=max_clips, aspect_ratio=aspect_ratio,
            selection_mode=selection_mode, caption_style=caption_style,
            effect=effect, framing=framing,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if Path(file.filename or "").suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
        raise HTTPException(400, "Upload MP4, MOV, MKV, or WEBM video")
    job_id, folder = _create("upload", opts, file.filename or "uploaded video")
    dest = folder / f"source{Path(file.filename or '').suffix.lower()}"
    length = 0
    try:
        with dest.open("wb") as output:
            while block := await file.read(1024 * 1024):
                length += len(block)
                if length > MAX_UPLOAD:
                    raise HTTPException(413, "Upload exceeds 2 GB")
                output.write(block)
        if not length:
            raise HTTPException(400, "Empty video file")
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        await file.close()
    POOL.submit(_process, folder, dest, None)
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs")
def list_jobs():
    jobs = []
    for job_file in DATA_DIR.glob("*/job.json"):
        try:
            jobs.append(_read(job_file.parent))
        except (ValueError, OSError):
            pass
    return sorted(jobs, key=lambda item: item["created_at"], reverse=True)[:50]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    folder = job_folder(job_id)
    return _read(folder)


@app.get("/api/jobs/{job_id}/clips/{filename}")
def get_clip(job_id: str, filename: str):
    folder = job_folder(job_id)
    job = _read(folder)
    allowed = {c["filename"] for c in job["clips"]}
    allowed.update(c["thumbnail"] for c in job["clips"] if c.get("thumbnail"))
    if filename not in allowed or Path(filename).name != filename:
        raise HTTPException(404, "Clip not found")
    path = folder / "clips" / filename
    if not path.is_file():
        raise HTTPException(404, "Clip not found")
    return FileResponse(path, media_type="image/jpeg" if filename.endswith(".jpg") else "video/mp4")


@app.get("/api/jobs/{job_id}/export")
def export_job(job_id: str):
    folder = job_folder(job_id)
    job = _read(folder)
    if job["status"] != "completed":
        raise HTTPException(409, "Job is not completed")
    zip_path = folder / "clipforge-clips.zip"
    if not zip_path.exists():
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as bundle:
            for clip in job["clips"]:
                bundle.write(folder / "clips" / clip["filename"], arcname=clip["filename"])
    return FileResponse(zip_path, filename=f"clipforge-{job_id[:8]}.zip", media_type="application/zip")



def _publication_update(folder: Path, pub_id: str, **changes) -> None:
    with LOCK:
        job = _read(folder)
        for publication in job.get("publications", []):
            if publication["id"] == pub_id:
                publication.update(changes)
                break
        _write(folder, job)


def _publish_worker(folder: Path, pub_id: str, platform: str, path: Path,
                    title: str, description: str, visibility: str) -> None:
    try:
        _publication_update(folder, pub_id, state="uploading")
        outcome = publish(platform, path, title, description, visibility)
        _publication_update(folder, pub_id, **outcome)
    except Exception as exc:
        LOGGER.exception("ClipForge publication %s failed", pub_id)
        _publication_update(folder, pub_id, state="failed",
                            error=f"{type(exc).__name__}: {str(exc)[:350]}")


@app.post("/api/jobs/{job_id}/publish", status_code=202)
def publish_clip(job_id: str, body: PublishRequest):
    if not body.approved:
        raise HTTPException(400, "Confirm approval before submitting to an external platform")
    folder = job_folder(job_id)
    with LOCK:
        job = _read(folder)
        if job["status"] != "completed":
            raise HTTPException(409, "Clip job is not completed")
        if body.filename not in {c["filename"] for c in job["clips"]}:
            raise HTTPException(404, "Clip is not in this job")
        if len(job.get("publications", [])) >= 100:
            raise HTTPException(429, "Publishing history limit reached")
        if not any(p["id"] == body.platform and p["configured"] for p in platform_status()):
            raise HTTPException(409, "Platform credentials are not configured on this server")
        pub_id = uuid.uuid4().hex
        job.setdefault("publications", []).append({
            "id": pub_id, "platform": body.platform, "filename": body.filename,
            "title": body.title, "state": "queued", "visibility": body.visibility,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        _write(folder, job)
    PUBLISH_POOL.submit(
        _publish_worker, folder, pub_id, body.platform, folder / "clips" / body.filename,
        body.title, body.description, body.visibility,
    )
    return {"id": pub_id, "state": "queued"}


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    folder = job_folder(job_id)
    job = _read(folder)
    if job["status"] in {"queued", "downloading", "analyzing", "transcribing",
                         "selecting", "rendering"} or any(
        p["state"] in {"queued", "uploading"} for p in job.get("publications", [])
    ):
        raise HTTPException(409, "Cannot delete a running job or upload")
    shutil.rmtree(folder)
