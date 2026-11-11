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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from clipforge import __version__
from clipforge.ai import ai_capabilities, select_ai_highlights, transcribe
from clipforge.captions import write_captions, write_hook, write_narration_captions
from clipforge.engine import (
    audio_energy,
    download_video,
    probe,
    render_clip,
    render_thumbnail,
    select_highlights,
    validate_url,
)
from clipforge.import_auth import youtube_cookie_status
from clipforge.narration import (
    audio_duration,
    build_script,
    narration_status,
    require_voice_config,
    synthesize,
)
from clipforge.publishing import platform_status, publish
from clipforge.recovery import load_json, recover_source, save_json

DATA_DIR = Path(os.environ.get("CLIPFORGE_DATA_DIR", "./clipforge-data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = 2 * 1024 * 1024 * 1024
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge")
PUBLISH_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-publish")
LOCK = threading.RLock()
STATIC = Path(__file__).resolve().parent / "static"
LOGGER = logging.getLogger("uvicorn.error")

ACTIVE_STATES = {"queued", "downloading", "analyzing", "transcribing", "selecting", "rendering"}


@asynccontextmanager
async def lifespan(_application: FastAPI):
    """Requeue jobs interrupted by a container restart; failed jobs need approval."""
    recover_interrupted_jobs()
    yield


app = FastAPI(
    title="ClipForge", version=__version__, docs_url="/api/docs", lifespan=lifespan,
)
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


class Options(BaseModel):
    clip_seconds: int = Field(default=30, ge=10, le=120)
    max_clips: int = Field(default=25, ge=1, le=100)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    selection_mode: Literal["ai", "energy"] = "energy"
    caption_style: Literal["none", "classic", "bold", "neon"] = "none"
    effect: Literal["none", "pop", "cinema", "noir"] = "none"
    framing: Literal["pad", "crop"] = "pad"
    hook_style: Literal["none", "headline"] = "none"
    narration: Literal["none", "script", "narrated"] = "none"
    narration_style: Literal["energetic", "documentary", "casual"] = "energetic"
    narration_language: Literal["en", "es"] = "en"


class UrlRequest(Options):
    url: str
    rights_confirmed: bool = False


class ClipEditRequest(BaseModel):
    """Non-destructive clip revision; narrator use requires explicit consent."""

    start: float = Field(ge=0)
    duration: float = Field(ge=3, le=120)
    title: str = Field(default="", max_length=100)
    effect: Literal["none", "pop", "cinema", "noir"] = "none"
    framing: Literal["pad", "crop"] = "crop"
    caption_style: Literal["none", "classic", "bold", "neon"] = "bold"
    hook_style: Literal["none", "headline"] = "headline"
    script: str = Field(default="", max_length=1200)
    voiceover: bool = False
    voiceover_approved: bool = False


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
        "source_url": label if kind == "url" else None,
        "resume_count": 0,
        "options": options.model_dump(),
    })
    return job_id, folder


def _stage(folder: Path, status: str, progress: int) -> None:
    """Keep already-earned progress during checkpoint recovery."""
    with LOCK:
        job = _read(folder)
        job.update(status=status, progress=max(job.get("progress", 0), progress))
        _write(folder, job)


def _process(folder: Path, source_path: Path | None = None, url: str | None = None) -> None:
    """Resume from persisted media, transcript, AI scores and finished renders."""
    try:
        job = _read(folder)
        if job["status"] == "completed":
            return
        opts = Options(**job["options"])
        if opts.narration == "narrated":
            require_voice_config()
        LOGGER.info("ClipForge job %s started or resumed", folder.name)
        source_path = source_path or recover_source(folder, job)
        if source_path is None and job["source"] == "url":
            url = validate_url(url or job.get("source_url") or job["label"])
            _stage(folder, "downloading", 3)
            source_path = download_video(url, folder)
        if source_path is None:
            raise FileNotFoundError(
                "Original upload is unavailable. Submit this video again; "
                "incomplete browser uploads cannot yet be resumed."
            )
        _stage(folder, "analyzing", 10)
        info = probe(source_path)
        # Only record a completed source after ffprobe accepts it.
        _patch(folder, source_file=source_path.name)

        energy = []
        if info["has_audio"]:
            cached = load_json(folder / "audio-energy.json")
            if isinstance(cached, list) and all(
                isinstance(value, (int, float)) for value in cached
            ):
                energy = cached
            else:
                try:
                    energy = audio_energy(source_path, info["duration"])
                except (OSError, RuntimeError):
                    LOGGER.warning("Audio energy unavailable for %s", folder.name)
                save_json(folder / "audio-energy.json", energy)

        transcript = []
        if (opts.selection_mode == "ai" or opts.caption_style != "none"
                or opts.narration != "none"):
            if not info["has_audio"]:
                raise RuntimeError("Speech transcription needs a video with audio")
            _stage(folder, "transcribing", 12)
            saved_transcript = load_json(folder / "transcript.json")
            if isinstance(saved_transcript, list):
                transcript = saved_transcript
                LOGGER.info("Reused transcription for ClipForge job %s", folder.name)
            else:
                LOGGER.info("Transcribing ClipForge job %s", folder.name)
                transcript = transcribe(source_path, DATA_DIR / "models")
                save_json(folder / "transcript.json", transcript)

        candidates = load_json(folder / "candidates.json")
        if not isinstance(candidates, list) or not candidates or not all(
            isinstance(c, dict) and isinstance(c.get("start"), (float, int))
            and isinstance(c.get("duration"), (float, int)) for c in candidates
        ):
            if opts.selection_mode == "ai":
                _stage(folder, "selecting", 16)
                candidates = select_ai_highlights(
                    transcript, info["duration"], opts.clip_seconds, opts.max_clips,
                    energy, folder / "ai-scores.json",
                )
            else:
                candidates = select_highlights(
                    info["duration"], opts.clip_seconds, opts.max_clips, energy,
                )
            if not candidates:
                raise ValueError("No suitable clips were found")
            save_json(folder / "candidates.json", candidates)
        else:
            LOGGER.info("Reused %d selected clips for job %s", len(candidates), folder.name)

        output = folder / "clips"
        output.mkdir(exist_ok=True)
        _stage(folder, "rendering", 20)
        _patch(folder, duration=round(info["duration"], 2))
        LOGGER.info("ClipForge job %s rendering/checking %s clips", folder.name, len(candidates))
        previous = {
            c.get("number"): c for c in _read(folder).get("clips", [])
            if isinstance(c, dict)
        }
        clips = []
        for index, candidate in enumerate(candidates, 1):
            filename = f"clip-{index:03d}.mp4"
            thumbnail = f"clip-{index:03d}.jpg"
            destination = output / filename
            cached_clip = previous.get(index)
            can_skip = (
                cached_clip is not None
                and cached_clip.get("filename") == filename
                and cached_clip.get("start") == candidate["start"]
                and cached_clip.get("duration") == candidate["duration"]
                and destination.is_file()
                and destination.stat().st_size > 0
                and cached_clip.get("narration", "none") == opts.narration
            )
            if can_skip:
                try:
                    probe(destination)
                except (OSError, RuntimeError, ValueError):
                    can_skip = False
            if can_skip:
                LOGGER.info("Reused rendered clip %s for job %s", filename, folder.name)
                if cached_clip.get("thumbnail") and not (output / thumbnail).is_file():
                    try:
                        render_thumbnail(destination, output / thumbnail)
                    except (OSError, RuntimeError):
                        cached_clip["thumbnail"] = None
                clips.append(cached_clip)
            else:
                script = None
                voiceover = None
                if opts.narration != "none":
                    narration_dir = folder / "narration"
                    narration_dir.mkdir(exist_ok=True)
                    script = build_script(
                        transcript, candidate["start"], candidate["duration"],
                        opts.narration_style, opts.narration_language,
                        narration_dir / f"clip-{index:03d}.json",
                    )
                    if opts.narration == "narrated":
                        voiceover = synthesize(
                            script, narration_dir / f"clip-{index:03d}.mp3",
                        )
                subtitles = None
                if opts.caption_style != "none":
                    ass = output / f"clip-{index:03d}.ass"
                    if voiceover is not None:
                        added = write_narration_captions(
                            script, audio_duration(voiceover),
                            candidate["duration"], opts.caption_style, ass,
                        )
                    else:
                        added = write_captions(
                            transcript, candidate["start"], candidate["duration"],
                            opts.caption_style, ass,
                        )
                    if added:
                        subtitles = ass
                hook_file = None
                if opts.hook_style == "headline" and candidate.get("title"):
                    proposed = output / f"clip-{index:03d}-hook.ass"
                    if write_hook(candidate["title"], candidate["duration"], proposed):
                        hook_file = proposed
                # A crash during FFmpeg must not leave a supposedly valid clip.
                partial = output / f".clip-{index:03d}.partial.mp4"
                partial.unlink(missing_ok=True)
                render_clip(
                    source_path, partial, candidate["start"], candidate["duration"],
                    opts.aspect_ratio, captions=subtitles, effect=opts.effect,
                    framing=opts.framing, voiceover=voiceover,
                    has_source_audio=info["has_audio"], hook=hook_file,
                )
                os.replace(partial, destination)
                try:
                    render_thumbnail(destination, output / thumbnail)
                except (OSError, RuntimeError):
                    thumbnail = None
                clips.append({
                    "filename": filename, "thumbnail": thumbnail, "number": index,
                    "captions_added": subtitles is not None, "effect": opts.effect,
                    "framing": opts.framing, "narration": opts.narration,
                    "script": script, "voiceover_added": voiceover is not None,
                    "hook_style": opts.hook_style if hook_file else "none",
                    "caption_style": opts.caption_style if subtitles else "none",
                    **candidate,
                })
            _patch(folder, clips=clips, progress=max(
                _read(folder).get("progress", 0),
                20 + int(index / len(candidates) * 79),
            ))
        _patch(folder, status="completed", progress=100, error=None)
        LOGGER.info("ClipForge job %s completed", folder.name)
    except Exception as exc:
        LOGGER.exception("ClipForge job %s failed", folder.name)
        _patch(folder, status="failed",
               error=f"{type(exc).__name__}: {str(exc)[:500]}")


def recover_interrupted_jobs() -> None:
    """Auto-resume queued/in-progress jobs after a process/container restart."""
    for job_file in sorted(DATA_DIR.glob("*/job.json")):
        folder = job_file.parent
        try:
            with LOCK:
                job = _read(folder)
                if job.get("status") in ACTIVE_STATES:
                    job.update(status="queued", error=None,
                               resume_count=job.get("resume_count", 0) + 1)
                    _write(folder, job)
                    POOL.submit(_process, folder)
                    LOGGER.info("Requeued interrupted ClipForge job %s", folder.name)
                elif job.get("status") == "completed":
                    dirty = False
                    for clip in job.get("clips", []):
                        if clip.get("revision_state") not in {"queued", "rendering"}:
                            continue
                        request = clip.get("revision_request")
                        if not isinstance(request, dict):
                            clip.update(
                                revision_state="failed",
                                revision_error="Interrupted revision has no saved edit request",
                            )
                        elif request.get("voiceover"):
                            # Never automatically retry a potentially billed API operation.
                            clip.update(
                                revision_state="failed",
                                revision_error=(
                                    "Interrupted voiceover generation. Original clip "
                                    "is intact; review and submit a new edit if desired."
                                ),
                            )
                        else:
                            clip["revision_state"] = "queued"
                            POOL.submit(
                                _edit_clip_worker, folder, clip["number"], request,
                            )
                        dirty = True
                    if dirty:
                        _write(folder, job)
        except (OSError, ValueError, KeyError):
            LOGGER.exception("Unable to recover job %s", folder.name)


@app.get("/")
@app.get("/studio")
@app.get("/library")
@app.get("/publishing")
@app.get("/activity")
@app.get("/settings")
def index():
    """Distinct navigable pages share one UI shell and static assets."""
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


@app.get("/api/settings")
def get_settings():
    saved = load_json(DATA_DIR / "settings.json")
    try:
        defaults = Options(**saved) if isinstance(saved, dict) else Options(
            selection_mode="ai", caption_style="bold", effect="pop",
            framing="crop", max_clips=5, hook_style="headline",
        )
    except ValueError:
        defaults = Options()
    return {
        "defaults": defaults.model_dump(),
        "ai": ai_capabilities(),
        "narration": narration_status(),
        "youtube_import": youtube_cookie_status(),
    }


@app.put("/api/settings")
def save_settings(defaults: Options):
    """Persist nonsecret Studio defaults. Secrets stay in server environment."""
    save_json(DATA_DIR / "settings.json", defaults.model_dump())
    return {"defaults": defaults.model_dump()}


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
    hook_style: str = Form("none"),
    narration: str = Form("none"),
    narration_style: str = Form("energetic"),
    narration_language: str = Form("en"),
    rights_confirmed: bool = Form(False),
):
    if not rights_confirmed:
        raise HTTPException(400, "Confirm you own or have permission to clip this video")
    try:
        opts = Options(
            clip_seconds=clip_seconds, max_clips=max_clips, aspect_ratio=aspect_ratio,
            selection_mode=selection_mode, caption_style=caption_style,
            effect=effect, framing=framing, hook_style=hook_style,
            narration=narration, narration_style=narration_style,
            narration_language=narration_language,
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


@app.post("/api/jobs/{job_id}/resume", status_code=202)
def resume_job(job_id: str):
    """Manual recovery of a failed job; never start the same job twice."""
    folder = job_folder(job_id)
    with LOCK:
        job = _read(folder)
        if job["status"] != "failed":
            raise HTTPException(409, "Only failed jobs can be resumed manually")
        job.update(status="queued", error=None,
                   resume_count=job.get("resume_count", 0) + 1)
        _write(folder, job)
        POOL.submit(_process, folder)
        return {"id": job_id, "status": "queued", "resume_count": job["resume_count"]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    folder = job_folder(job_id)
    return _read(folder)


def _edit_clip_worker(folder: Path, number: int, request: dict) -> None:
    """Render versioned media and atomically replace the active clip metadata.

    Original MP4s are retained in revision history. A failed edit never
    destroys a published or downloaded prior version.
    """
    destination = None
    try:
        with LOCK:
            job = _read(folder)
            clip = next(c for c in job["clips"] if c["number"] == number)
            version = clip.get("revision_count", 0) + 1
            clip["revision_state"] = "rendering"
            _write(folder, job)
        edit = ClipEditRequest(**request)
        source = recover_source(folder, job)
        if source is None:
            raise FileNotFoundError("Original source is missing; the saved clip is unchanged")
        info = probe(source)
        if edit.start + edit.duration > info["duration"] + 0.05:
            raise ValueError("Clip trim extends past the end of the original video")
        output = folder / "clips"
        base = f"clip-{number:03d}-v{version:02d}"
        destination = output / f"{base}.mp4"
        partial = output / f".{base}.partial.mp4"
        partial.unlink(missing_ok=True)

        voiceover = None
        subtitles = None
        if edit.voiceover:
            if not edit.voiceover_approved or not edit.script.strip():
                raise ValueError("Voiceover requires a script and explicit API-charge approval")
            require_voice_config()
            narration_dir = folder / "narration"
            narration_dir.mkdir(exist_ok=True)
            voiceover = synthesize(edit.script.strip(), narration_dir / f"{base}.mp3")
        if edit.caption_style != "none":
            ass = output / f"{base}.ass"
            if voiceover is not None:
                added = write_narration_captions(
                    edit.script, audio_duration(voiceover),
                    edit.duration, edit.caption_style, ass,
                )
            else:
                transcript = load_json(folder / "transcript.json")
                if not isinstance(transcript, list):
                    if not info["has_audio"]:
                        raise ValueError("Original video has no speech for automatic captions")
                    transcript = transcribe(source, DATA_DIR / "models")
                    save_json(folder / "transcript.json", transcript)
                added = write_captions(
                    transcript, edit.start, edit.duration, edit.caption_style, ass,
                )
            if added:
                subtitles = ass

        hook_file = None
        if edit.hook_style == "headline" and edit.title.strip():
            ass = output / f"{base}-hook.ass"
            if write_hook(edit.title, edit.duration, ass):
                hook_file = ass

        render_clip(
            source, partial, edit.start, edit.duration, job["options"]["aspect_ratio"],
            captions=subtitles, effect=edit.effect, framing=edit.framing,
            voiceover=voiceover, has_source_audio=info["has_audio"], hook=hook_file,
        )
        os.replace(partial, destination)
        thumbnail_path = output / f"{base}.jpg"
        thumbnail = None
        try:
            render_thumbnail(destination, thumbnail_path)
            thumbnail = thumbnail_path.name
        except (OSError, RuntimeError):
            LOGGER.warning("Thumbnail unavailable for revised clip %s", base)
        # Commit metadata only after a playable final MP4 exists.
        with LOCK:
            current = _read(folder)
            target = next(c for c in current["clips"] if c["number"] == number)
            history = target.get("revision_history", [])
            history.append({
                key: target.get(key) for key in (
                    "filename", "thumbnail", "start", "duration", "title",
                    "effect", "framing", "captions_added", "voiceover_added",
                    "hook_style", "script",
                )
            })
            target.update(
                filename=destination.name, thumbnail=thumbnail,
                start=round(edit.start, 2), duration=round(edit.duration, 2),
                title=edit.title.strip(), effect=edit.effect, framing=edit.framing,
                captions_added=subtitles is not None,
                caption_style=edit.caption_style,
                hook_style=edit.hook_style if hook_file else "none",
                voiceover_added=voiceover is not None,
                narration="narrated" if voiceover else ("script" if edit.script.strip() else "none"),
                script=edit.script.strip() or None,
                revision_count=version, revision_history=history,
                revision_state="completed", revision_error=None,
                revision_request=None,
            )
            _write(folder, current)
            # Existing ZIP must not return outdated clips after edits.
            (folder / "clipforge-clips.zip").unlink(missing_ok=True)
        LOGGER.info("ClipForge clip %s revision %d complete", number, version)
    except Exception as exc:
        LOGGER.exception("ClipForge clip %s revision failed", number)
        with LOCK:
            try:
                job = _read(folder)
                clip = next(c for c in job["clips"] if c["number"] == number)
                clip["revision_state"] = "failed"
                clip["revision_error"] = f"{type(exc).__name__}: {str(exc)[:350]}"
                # Keep the request for diagnosis, but never retry charged TTS automatically.
                _write(folder, job)
            except (FileNotFoundError, StopIteration, ValueError):
                LOGGER.warning("Clip metadata disappeared during revision failure")
    finally:
        if destination is not None:
            (folder / "clips" / ("." + destination.stem + ".partial.mp4")).unlink(
                missing_ok=True
            )


@app.post("/api/jobs/{job_id}/clips/{number}/edit", status_code=202)
def edit_clip(job_id: str, number: int, body: ClipEditRequest):
    """Queue one individual re-render; other clips and publications remain intact."""
    folder = job_folder(job_id)
    with LOCK:
        job = _read(folder)
        if job["status"] != "completed":
            raise HTTPException(409, "Only completed jobs can be edited")
        clip = next((c for c in job["clips"] if c.get("number") == number), None)
        if clip is None:
            raise HTTPException(404, "Clip not found")
        if clip.get("revision_state") in {"queued", "rendering"}:
            raise HTTPException(409, "This clip is already being edited")
        if clip.get("revision_count", 0) >= 20:
            raise HTTPException(429, "Maximum 20 revisions per clip")
        if body.start + body.duration > float(job.get("duration") or 0) + 0.05:
            raise HTTPException(422, "Trim must fit inside the original video's duration")
        if body.voiceover and (not body.voiceover_approved or not body.script.strip()):
            raise HTTPException(400, "Explicit approval and a script are required for paid TTS")
        if body.voiceover:
            try:
                require_voice_config()
            except (ValueError, RuntimeError) as exc:
                raise HTTPException(409, str(exc)) from exc
        clip.update(
            revision_state="queued", revision_error=None,
            revision_request=body.model_dump(),
        )
        _write(folder, job)
        POOL.submit(_edit_clip_worker, folder, number, body.model_dump())
    return {"id": job_id, "clip": number, "state": "queued"}


@app.get("/api/jobs/{job_id}/clips/{filename}")
def get_clip(job_id: str, filename: str):
    folder = job_folder(job_id)
    job = _read(folder)
    allowed = {c["filename"] for c in job["clips"]}
    allowed.update(c["thumbnail"] for c in job["clips"] if c.get("thumbnail"))
    for clip in job["clips"]:
        for prior in clip.get("revision_history", []):
            if prior.get("filename"):
                allowed.add(prior["filename"])
            if prior.get("thumbnail"):
                allowed.add(prior["thumbnail"])
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
    if any(c.get("revision_state") in {"queued", "rendering"}
           for c in job.get("clips", [])):
        raise HTTPException(409, "Cannot delete a job while a clip revision is running")
    if job["status"] in {"queued", "downloading", "analyzing", "transcribing",
                         "selecting", "rendering"} or any(
        p["state"] in {"queued", "uploading"} for p in job.get("publications", [])
    ):
        raise HTTPException(409, "Cannot delete a running job or upload")
    shutil.rmtree(folder)
