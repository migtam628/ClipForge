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
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from clipforge import __version__, admin_auth
from clipforge.ai import ai_capabilities, select_ai_highlights, transcribe
from clipforge.analytics import PLATFORMS, post_key, safe_csv, summarize, valid_https_url
from clipforge.captions import (
    write_brand_sticker,
    write_captions,
    write_hook,
    write_narration_captions,
    write_storyboard_captions,
)
from clipforge.config_agent import request_agent
from clipforge.creator_tools import NICHES, suggest_post_copy
from clipforge.engine import (
    audio_energy,
    download_video,
    probe,
    render_clip,
    render_thumbnail,
    select_highlights,
    validate_url,
)
from clipforge.environment import field_schema, validate_patch
from clipforge.import_auth import youtube_cookie_status
from clipforge.narration import (
    audio_duration,
    narration_status,
    require_voice_config,
    synthesize,
)
from clipforge.publishing import platform_status, publish
from clipforge.quality import inspect_rendered_clip, refine_speech_boundaries
from clipforge.recovery import load_json, recover_source, save_json
from clipforge.smart_frame import plan_face_tracking
from clipforge.storyboard import build_storyboard, synthesize_timed_voice, vision_status

DATA_DIR = Path(os.environ.get("CLIPFORGE_DATA_DIR", "./clipforge-data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = 2 * 1024 * 1024 * 1024
MAX_BATCH_SOURCES = 100
MAX_BATCH_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_QUEUED_SOURCES = 125
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge")
PUBLISH_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-publish")
PUBLISH_STOP = threading.Event()
LOCK = threading.RLock()
STATIC = Path(__file__).resolve().parent / "static"
LOGGER = logging.getLogger("uvicorn.error")

ACTIVE_STATES = {"queued", "downloading", "analyzing", "transcribing", "selecting", "rendering"}


@asynccontextmanager
async def lifespan(_application: FastAPI):
    """Recover local jobs, then run the persistent approved-publication scheduler."""
    recover_interrupted_jobs()
    recover_interrupted_publications()
    PUBLISH_STOP.clear()
    _dispatch_due_publications()
    schedule_thread = threading.Thread(
        target=_publishing_clock, name="clipforge-publications", daemon=True,
    )
    schedule_thread.start()
    try:
        yield
    finally:
        PUBLISH_STOP.set()
        schedule_thread.join(timeout=3)


app = FastAPI(
    title="ClipForge", version=__version__, docs_url="/api/docs", lifespan=lifespan,
)
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


class AdminLogin(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class EnvironmentUpdate(BaseModel):
    revision: str = Field(min_length=16, max_length=16)
    changes: dict[str, str] = Field(min_length=1, max_length=24)


class Options(BaseModel):
    clip_seconds: int = Field(default=30, ge=10, le=120)
    max_clips: int = Field(default=25, ge=1, le=100)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    selection_mode: Literal["ai", "energy"] = "energy"
    caption_style: Literal["none", "classic", "bold", "neon", "karaoke", "pop"] = "none"
    effect: Literal["none", "pop", "cinema", "noir"] = "none"
    framing: Literal["pad", "crop", "smart", "blur"] = "pad"
    hook_style: Literal["none", "headline"] = "none"
    narration: Literal["none", "script", "narrated"] = "none"
    narration_style: Literal["energetic", "documentary", "casual"] = "energetic"
    narration_language: Literal["en", "es"] = "en"
    narration_basis: Literal["video", "speech"] = "speech"
    clip_focus: str = Field(default="", max_length=400)
    content_niche: Literal[
        "general", "podcast", "gaming", "education", "comedy",
        "travel", "sports", "business", "music", "news",
    ] = "general"
    use_voice_markers: bool = True
    brand_text: str = Field(default="", max_length=36)


class CreatorProfile(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    content_niche: Literal[
        "general", "podcast", "gaming", "education", "comedy",
        "travel", "sports", "business", "music", "news",
    ] = "general"
    clip_focus: str = Field(default="", max_length=400)
    caption_style: Literal[
        "none", "classic", "bold", "neon", "karaoke", "pop",
    ] = "karaoke"
    framing: Literal["pad", "crop", "smart", "blur"] = "smart"
    effect: Literal["none", "pop", "cinema", "noir"] = "pop"
    hook_style: Literal["none", "headline"] = "headline"
    use_voice_markers: bool = True
    brand_text: str = Field(default="", max_length=36)


class UrlRequest(Options):
    url: str
    rights_confirmed: bool = False


class BatchUrlRequest(Options):
    urls: list[str] = Field(min_length=1, max_length=MAX_BATCH_SOURCES)
    rights_confirmed: bool = False
    voice_spend_approved: bool = False


class ClipEditRequest(BaseModel):
    """Non-destructive clip revision; narrator use requires explicit consent."""

    start: float = Field(ge=0)
    duration: float = Field(ge=3, le=120)
    title: str = Field(default="", max_length=100)
    effect: Literal["none", "pop", "cinema", "noir"] = "none"
    framing: Literal["pad", "crop", "smart", "blur"] = "crop"
    caption_style: Literal["none", "classic", "bold", "neon", "karaoke", "pop"] = "bold"
    hook_style: Literal["none", "headline"] = "headline"
    script: str = Field(default="", max_length=1200)
    brand_text: str = Field(default="", max_length=36)
    regenerate_storyboard: bool = False
    narration_basis: Literal["video", "speech"] = "video"
    voiceover: bool = False
    voiceover_approved: bool = False


class AnalyticsEntryRequest(BaseModel):
    platform: Literal[
        "youtube", "instagram", "facebook", "tiktok", "x",
        "threads", "snapchat", "other",
    ]
    post_url: str = Field(default="", max_length=700)
    job_id: str = Field(default="", max_length=32)
    filename: str = Field(default="", max_length=90)
    views: int = Field(default=0, ge=0, le=2_000_000_000)
    likes: int = Field(default=0, ge=0, le=2_000_000_000)
    comments: int = Field(default=0, ge=0, le=2_000_000_000)
    shares: int = Field(default=0, ge=0, le=2_000_000_000)
    saves: int = Field(default=0, ge=0, le=2_000_000_000)
    earnings_usd: Decimal = Field(default=Decimal("0.00"), ge=0,
                                   max_digits=12, decimal_places=2)
    campaign_name: str = Field(default="", max_length=100)
    campaign_status: Literal[
        "none", "submitted", "approved", "rejected", "paid",
    ] = "none"
    evidence_url: str = Field(default="", max_length=700)
    notes: str = Field(default="", max_length=500)


class ScheduledPublishRequest(BaseModel):
    filename: str = Field(min_length=8, max_length=80)
    platforms: list[Literal["youtube", "instagram", "facebook", "tiktok"]] = Field(
        min_length=1, max_length=4,
    )
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2200)
    visibility: Literal["private", "unlisted", "public"] = "private"
    scheduled_at: datetime
    approved: bool = False


class PublishPreset(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    platforms: list[Literal["youtube", "instagram", "facebook", "tiktok"]] = Field(
        min_length=1, max_length=4,
    )
    visibility: Literal["private", "unlisted", "public"] = "private"
    description: str = Field(default="", max_length=2200)


class ReapproveRequest(BaseModel):
    approved: bool = False
    duplicate_checked: bool = False


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


def _vision_preflight(options: Options) -> dict | None:
    """Validate required local vision model before expensive processing."""
    if options.narration == "none" or options.narration_basis != "video":
        return None
    state = vision_status()
    if not state.get("installed"):
        return state
    return None


def _process(folder: Path, source_path: Path | None = None, url: str | None = None) -> None:
    """Resume from persisted media, transcript, AI scores and finished renders."""
    try:
        job = _read(folder)
        if job["status"] == "completed":
            return
        opts = Options(**job["options"])
        if opts.narration == "narrated":
            require_voice_config()
        missing_vision = _vision_preflight(opts)
        if missing_vision:
            raise RuntimeError(missing_vision["note"])
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
                visual_only = (opts.narration != "none"
                               and opts.narration_basis == "video"
                               and opts.selection_mode == "energy"
                               and (opts.caption_style == "none"
                                    or opts.narration == "narrated"))
                if not visual_only:
                    raise RuntimeError(
                        "Speech-based features need audio. For silent footage choose "
                        "Audio-energy selection and video-aware narration."
                    )
            else:
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
                    opts.clip_focus, opts.content_niche, opts.use_voice_markers,
                )
            else:
                candidates = select_highlights(
                    info["duration"], opts.clip_seconds, opts.max_clips, energy,
                )
            if not candidates:
                raise ValueError("No suitable clips were found")
            # Preserve the selected story while avoiding mid-sentence cuts.
            # Never move a boundary when a safe, non-overlapping trim is unavailable.
            candidates = refine_speech_boundaries(
                candidates, transcript, info["duration"],
            )
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
                storyboard = None
                timed_beats = None
                if opts.narration != "none":
                    narration_dir = folder / "narration"
                    narration_dir.mkdir(exist_ok=True)
                    storyboard = build_storyboard(
                        transcript, candidate["start"], candidate["duration"],
                        opts.narration_style, opts.narration_language,
                        narration_dir / f"clip-{index:03d}-story.json",
                        source=source_path,
                        basis="video" if opts.narration_basis == "video" else "transcript",
                    )
                    script = storyboard["script"]
                    if opts.narration == "narrated":
                        voiceover, timed_beats = synthesize_timed_voice(
                            storyboard, narration_dir,
                            f"clip-{index:03d}", candidate["duration"],
                        )
                subtitles = None
                if opts.caption_style != "none":
                    ass = output / f"clip-{index:03d}.ass"
                    if voiceover is not None:
                        added = write_storyboard_captions(
                            timed_beats, candidate["duration"], opts.caption_style, ass,
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
                brand_file = None
                if opts.brand_text.strip():
                    proposed = output / f"clip-{index:03d}-brand.ass"
                    if write_brand_sticker(
                        opts.brand_text, candidate["duration"], proposed,
                    ):
                        brand_file = proposed
                # A crash during FFmpeg must not leave a supposedly valid clip.
                partial = output / f".clip-{index:03d}.partial.mp4"
                partial.unlink(missing_ok=True)
                smart_track = None
                if opts.framing == "smart":
                    tracking_file = output / f"clip-{index:03d}-tracking.txt"
                    if plan_face_tracking(
                        source_path, candidate["start"], candidate["duration"],
                        tracking_file, *({"9:16": (720, 1280), "16:9": (1280, 720),
                                          "1:1": (720, 720)}[opts.aspect_ratio]),
                    ):
                        smart_track = tracking_file
                render_clip(
                    source_path, partial, candidate["start"], candidate["duration"],
                    opts.aspect_ratio, captions=subtitles, effect=opts.effect,
                    framing=opts.framing, voiceover=voiceover,
                    has_source_audio=info["has_audio"], hook=hook_file,
                    smart_track=smart_track, brand=brand_file,
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
                    "storyboard": storyboard["beats"] if storyboard else [],
                    "analysis_basis": storyboard["analysis_basis"] if storyboard else None,
                    "frame_samples": storyboard["frame_samples"] if storyboard else 0,
                    "creator_focus": opts.clip_focus,
                    "creator_niche": opts.content_niche,
                    "brand_text": opts.brand_text.strip(),
                    "hook_style": opts.hook_style if hook_file else "none",
                    "caption_style": opts.caption_style if subtitles else "none",
                    "face_tracking": smart_track is not None,
                    **candidate,
                })
            # Observable technical checks plus clearly marked editorial uncertainty.
            # This is an audit, not automatic approval for posting.
            active_clip = clips[-1]
            active_clip["quality_report"] = inspect_rendered_clip(
                destination,
                expected_duration=active_clip["duration"],
                aspect_ratio=opts.aspect_ratio,
                source_has_audio=info["has_audio"],
                narration=active_clip.get("narration", "none"),
                script=active_clip.get("script"),
                beats=active_clip.get("storyboard") or [],
                captions_requested=opts.caption_style != "none",
                captions_added=active_clip.get("captions_added", False),
                candidate={**active_clip, "creator_niche": opts.content_niche},
            )
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
                        if clip.get("post_copy_state") in {"queued", "generating"}:
                            clip["post_copy_state"] = "failed"
                            clip["post_copy_error"] = (
                                "Interrupted local AI publishing draft. "
                                "Select Draft post copy again to retry."
                            )
                            dirty = True
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
@app.get("/analytics")
@app.get("/settings")
def index():
    """Distinct navigable pages share one UI shell and static assets."""
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/api/admin/session")
def admin_session(request: Request):
    """Only session and CSRF metadata; never disclose the configured hash."""
    if not admin_auth.configured():
        return {"configured": False, "authenticated": False}
    if admin_auth.has_session(request):
        session = admin_auth.require_session(request)
        return {"configured": True, "authenticated": True, "csrf": session.csrf}
    return {"configured": True, "authenticated": False}


@app.post("/api/admin/login")
def admin_login(credentials: AdminLogin, request: Request, response: Response):
    csrf = admin_auth.sign_in(request, credentials.password, response)
    return {"authenticated": True, "csrf": csrf}


@app.post("/api/admin/logout")
def admin_logout(request: Request, response: Response):
    admin_auth.require_csrf(request)
    admin_auth.sign_out(request, response)
    return {"authenticated": False}


@app.get("/api/admin/environment")
def admin_environment(request: Request):
    admin_auth.require_session(request)
    try:
        result = request_agent("read")
    except ConnectionError as exc:
        raise HTTPException(503, "Host configuration agent not connected") from exc
    if not result.get("ok"):
        raise HTTPException(503, "Host configuration agent refused the request")
    return result["data"]


@app.get("/api/admin/environment/schema")
def admin_environment_schema(request: Request):
    admin_auth.require_session(request)
    return {"entries": field_schema(), "apply_mode": "manual_restart_when_idle"}


@app.post("/api/admin/environment/validate")
def admin_environment_validate(body: EnvironmentUpdate, request: Request):
    admin_auth.require_csrf(request)
    try:
        clean = validate_patch(body.changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"valid": True, "keys": sorted(clean),
            "apply_required": True, "requires_idle_queue": True}


@app.patch("/api/admin/environment")
def admin_environment_update(body: EnvironmentUpdate, request: Request):
    """Write to host .env through restricted agent; never restart Docker here."""
    admin_auth.require_csrf(request)
    try:
        clean = validate_patch(body.changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        result = request_agent("update", {
            "revision": body.revision, "changes": clean,
        })
    except ConnectionError as exc:
        raise HTTPException(503, "Host configuration agent not connected") from exc
    if not result.get("ok"):
        # Validation/conflict errors deliberately do not echo submitted values.
        raise HTTPException(409, result.get("error", "Config write failed"))
    return result["data"]


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
            selection_mode="ai", caption_style="karaoke", effect="pop",
            framing="smart", max_clips=5, hook_style="headline",
            narration_basis="video",
        )
    except ValueError:
        defaults = Options()
    return {
        "defaults": defaults.model_dump(),
        "ai": ai_capabilities(),
        "narration": narration_status(),
        "vision": vision_status(),
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


def _check_batch_capacity(count: int) -> None:
    """Avoid unbounded uploads/jobs on a single-worker home server."""
    if not 1 <= count <= MAX_BATCH_SOURCES:
        raise HTTPException(422, "A batch requires 1 to 100 source videos")
    queued = 0
    for item in DATA_DIR.glob("*/job.json"):
        try:
            if _read(item.parent).get("status") in ACTIVE_STATES:
                queued += 1
        except (ValueError, OSError):
            continue
    if queued + count > MAX_QUEUED_SOURCES:
        raise HTTPException(
            429, "Too many queued sources. Finish or remove jobs before adding more."
        )


@app.post("/api/jobs/batch", status_code=202)
def submit_urls_batch(body: BatchUrlRequest):
    """Queue up to 100 authorized URL sources, one job/checkpoint per source."""
    if not body.rights_confirmed:
        raise HTTPException(400, "Confirm rights for every source in the batch")
    if body.narration == "narrated" and not body.voice_spend_approved:
        raise HTTPException(400, "Approve possible voice API charges for batch narration")
    try:
        urls = list(dict.fromkeys(validate_url(url) for url in body.urls))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _check_batch_capacity(len(urls))
    options = Options(**body.model_dump())
    batch_id = uuid.uuid4().hex
    prepared = []
    try:
        for index, url in enumerate(urls, 1):
            job_id, folder = _create("url", options, url)
            prepared.append((job_id, folder, url))
            _patch(
                folder, batch_id=batch_id, batch_index=index,
                batch_total=len(urls),
            )
    except Exception:
        for _, folder, _ in prepared:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    for _, folder, url in prepared:
        POOL.submit(_process, folder, None, url)
    return {
        "batch_id": batch_id, "job_ids": [item[0] for item in prepared],
        "accepted": len(prepared), "status": "queued",
    }


@app.post("/api/uploads/batch", status_code=202)
async def submit_upload_batch(
    files: list[UploadFile] = File(...),
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
    narration_basis: str = Form("speech"),
    clip_focus: str = Form(""),
    content_niche: str = Form("general"),
    use_voice_markers: bool = Form(True),
    brand_text: str = Form(""),
    rights_confirmed: bool = Form(False),
    voice_spend_approved: bool = Form(False),
):
    """Bounded multi-file upload with rollback and one persistent job per video."""
    if not rights_confirmed:
        raise HTTPException(400, "Confirm rights for all uploaded files")
    try:
        options = Options(
            clip_seconds=clip_seconds, max_clips=max_clips,
            aspect_ratio=aspect_ratio, selection_mode=selection_mode,
            caption_style=caption_style, effect=effect,
            framing=framing, hook_style=hook_style,
            narration=narration, narration_style=narration_style,
            narration_language=narration_language, narration_basis=narration_basis,
            clip_focus=clip_focus, content_niche=content_niche,
            use_voice_markers=use_voice_markers, brand_text=brand_text,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if options.narration == "narrated" and not voice_spend_approved:
        raise HTTPException(400, "Approve possible voice API charges for batch narration")
    _check_batch_capacity(len(files))
    for item in files:
        filename = (item.filename or "").replace("\\", "/").split("/")[-1]
        if Path(filename).suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
            raise HTTPException(400, "Every source must be MP4, MOV, MKV or WEBM")
    batch_id = uuid.uuid4().hex
    prepared = []
    total_bytes = 0
    try:
        for index, item in enumerate(files, 1):
            filename = (item.filename or "uploaded-video").replace("\\", "/").split("/")[-1]
            suffix = Path(filename).suffix.lower()
            job_id, folder = _create("upload", options, filename)
            destination = folder / f"source{suffix}"
            prepared.append((job_id, folder, destination))
            _patch(
                folder, batch_id=batch_id, batch_index=index,
                batch_total=len(files),
            )
            length = 0
            with destination.open("wb") as output:
                while block := await item.read(1024 * 1024):
                    length += len(block)
                    total_bytes += len(block)
                    if length > MAX_UPLOAD or total_bytes > MAX_BATCH_TOTAL_BYTES:
                        raise HTTPException(
                            413, "Each video must be <=2 GB and the batch <=8 GB"
                        )
                    output.write(block)
            if not length:
                raise HTTPException(400, "Empty video in batch")
    except Exception:
        for _, folder, _ in prepared:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        for item in files:
            await item.close()
    for _, folder, destination in prepared:
        POOL.submit(_process, folder, destination, None)
    return {
        "batch_id": batch_id, "job_ids": [item[0] for item in prepared],
        "accepted": len(prepared), "status": "queued",
    }


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
    narration_basis: str = Form("speech"),
    clip_focus: str = Form(""),
    content_niche: str = Form("general"),
    use_voice_markers: bool = Form(True),
    brand_text: str = Form(""),
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
            narration_language=narration_language, narration_basis=narration_basis,
            clip_focus=clip_focus, content_niche=content_niche,
            use_voice_markers=use_voice_markers, brand_text=brand_text,
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


@app.get("/api/creator/profiles")
def list_creator_profiles():
    items = load_json(DATA_DIR / "creator-profiles.json")
    return items if isinstance(items, list) else []


@app.put("/api/creator/profiles")
def save_creator_profile(profile: CreatorProfile):
    """Save local, non-secret channel preferences; no external account access."""
    with LOCK:
        records = list_creator_profiles()
        records = [row for row in records if row.get("name") != profile.name]
        if len(records) >= 12:
            raise HTTPException(429, "Maximum 12 saved creator profiles")
        entry = profile.model_dump()
        if entry["content_niche"] not in NICHES:
            raise HTTPException(422, "Unsupported creator niche")
        records.append(entry)
        save_json(DATA_DIR / "creator-profiles.json", records)
    return entry


def _post_copy_worker(folder: Path, number: int) -> None:
    """Generate an optional reviewable publishing draft with local Ollama."""
    try:
        with LOCK:
            job = _read(folder)
            clip = next(c for c in job["clips"] if c["number"] == number)
            clip["post_copy_state"] = "generating"
            _write(folder, job)
        transcript = load_json(folder / "transcript.json")
        if not isinstance(transcript, list):
            transcript = []
            source = recover_source(folder, job)
            if source is not None and probe(source)["has_audio"]:
                transcript = transcribe(source, DATA_DIR / "models")
                save_json(folder / "transcript.json", transcript)
        opts = Options(**job["options"])
        draft = suggest_post_copy(
            clip, transcript, opts.content_niche, opts.narration_language,
            folder / f"post-copy-{number:03d}.json",
        )
        with LOCK:
            latest = _read(folder)
            target = next(c for c in latest["clips"] if c["number"] == number)
            target.update(
                post_copy=draft, post_copy_state="completed", post_copy_error=None,
            )
            _write(folder, latest)
    except Exception as exc:
        LOGGER.exception("Clip publishing draft generation failed")
        with LOCK:
            try:
                job = _read(folder)
                clip = next(c for c in job["clips"] if c["number"] == number)
                clip.update(post_copy_state="failed",
                            post_copy_error=f"{type(exc).__name__}: {str(exc)[:260]}")
                _write(folder, job)
            except (OSError, ValueError, StopIteration):
                pass


@app.post("/api/jobs/{job_id}/clips/{number}/post-copy", status_code=202)
def request_post_copy(job_id: str, number: int):
    """Never publish; only prepare a human-reviewable suggested caption."""
    folder = job_folder(job_id)
    with LOCK:
        job = _read(folder)
        if job["status"] != "completed":
            raise HTTPException(409, "Complete rendering before preparing post copy")
        clip = next((c for c in job["clips"] if c.get("number") == number), None)
        if clip is None:
            raise HTTPException(404, "Clip not found")
        if clip.get("post_copy_state") in {"queued", "generating"}:
            raise HTTPException(409, "Publishing copy generation is already in progress")
        if clip.get("revision_state") in {"queued", "rendering"}:
            raise HTTPException(409, "Finish the clip revision before writing post copy")
        clip.update(post_copy_state="queued", post_copy_error=None)
        _write(folder, job)
    POOL.submit(_post_copy_worker, folder, number)
    return {"job_id": job_id, "clip": number, "status": "queued"}


@app.get("/api/jobs")
def list_jobs():
    jobs = []
    for job_file in DATA_DIR.glob("*/job.json"):
        try:
            jobs.append(_read(job_file.parent))
        except (ValueError, OSError):
            pass
    return sorted(jobs, key=lambda item: item["created_at"], reverse=True)[:250]


@app.get("/api/batches")
def list_batches():
    """Summaries only; individual job statuses still come from /api/jobs."""
    grouped = {}
    for job in list_jobs():
        batch_id = job.get("batch_id")
        if not batch_id:
            continue
        batch = grouped.setdefault(batch_id, {
            "id": batch_id, "total": job.get("batch_total", 0),
            "completed": 0, "failed": 0, "queued": 0, "running": 0,
            "created_at": job["created_at"],
        })
        state = job.get("status")
        if state == "completed":
            batch["completed"] += 1
        elif state == "failed":
            batch["failed"] += 1
        elif state == "queued":
            batch["queued"] += 1
        else:
            batch["running"] += 1
        batch["created_at"] = min(batch["created_at"], job["created_at"])
    return sorted(grouped.values(), key=lambda x: x["created_at"], reverse=True)


@app.post("/api/jobs/{job_id}/resume", status_code=202)
def resume_job(job_id: str):
    """Fail immediately on missing prerequisites, keeping failed-job checkpoints."""
    folder = job_folder(job_id)
    with LOCK:
        job = _read(folder)
        if job["status"] != "failed":
            raise HTTPException(409, "Only failed jobs can be resumed manually")
        options = Options(**job["options"])
    # Check Ollama outside the global job lock (the network may take seconds).
    missing_vision = _vision_preflight(options)
    if missing_vision:
        raise HTTPException(
            409, "Cannot resume yet: " + missing_vision["note"],
        )
    with LOCK:
        job = _read(folder)
        if job["status"] != "failed":
            raise HTTPException(409, "Job is already being processed")
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
        storyboard = None
        timed_beats = None
        narration_dir = folder / "narration"
        if edit.regenerate_storyboard:
            narration_dir.mkdir(exist_ok=True)
            settings = Options(**job["options"])
            transcript = load_json(folder / "transcript.json")
            if not isinstance(transcript, list):
                transcript = transcribe(source, DATA_DIR / "models") if info["has_audio"] else []
                save_json(folder / "transcript.json", transcript)
            storyboard = build_storyboard(
                transcript, edit.start, edit.duration,
                settings.narration_style, settings.narration_language,
                narration_dir / f"{base}-story.json",
                source=source,
                basis="video" if edit.narration_basis == "video" else "transcript",
            )
        if edit.voiceover:
            if not edit.voiceover_approved or not (edit.script.strip() or storyboard):
                raise ValueError("Voiceover requires approved charges and a script")
            require_voice_config()
            narration_dir.mkdir(exist_ok=True)
            if storyboard:
                voiceover, timed_beats = synthesize_timed_voice(
                    storyboard, narration_dir, base, edit.duration,
                )
            else:
                voiceover = synthesize(edit.script.strip(), narration_dir / f"{base}.mp3")
        if edit.caption_style != "none":
            ass = output / f"{base}.ass"
            if voiceover is not None:
                if timed_beats is not None:
                    added = write_storyboard_captions(
                        timed_beats, edit.duration, edit.caption_style, ass,
                    )
                else:
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

        brand_file = None
        if edit.brand_text.strip():
            proposed = output / f"{base}-brand.ass"
            if write_brand_sticker(edit.brand_text, edit.duration, proposed):
                brand_file = proposed
        smart_track = None
        if edit.framing == "smart":
            track = output / f"{base}-tracking.txt"
            dimensions = {
                "9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720),
            }[job["options"]["aspect_ratio"]]
            if plan_face_tracking(source, edit.start, edit.duration, track, *dimensions):
                smart_track = track
        render_clip(
            source, partial, edit.start, edit.duration, job["options"]["aspect_ratio"],
            captions=subtitles, effect=edit.effect, framing=edit.framing,
            voiceover=voiceover, has_source_audio=info["has_audio"], hook=hook_file,
            smart_track=smart_track, brand=brand_file,
        )
        os.replace(partial, destination)
        thumbnail_path = output / f"{base}.jpg"
        thumbnail = None
        try:
            render_thumbnail(destination, thumbnail_path)
            thumbnail = thumbnail_path.name
        except (OSError, RuntimeError):
            LOGGER.warning("Thumbnail unavailable for revised clip %s", base)
        revised_narration = (
            "narrated" if voiceover else ("script" if (storyboard or edit.script.strip()) else "none")
        )
        revised_script = storyboard["script"] if storyboard else edit.script.strip() or None
        revised_beats = storyboard["beats"] if storyboard else []
        quality_report = inspect_rendered_clip(
            destination,
            expected_duration=edit.duration,
            aspect_ratio=job["options"]["aspect_ratio"],
            source_has_audio=info["has_audio"],
            narration=revised_narration,
            script=revised_script,
            beats=revised_beats,
            captions_requested=edit.caption_style != "none",
            captions_added=subtitles is not None,
            candidate={"creator_niche": job["options"].get("content_niche", "general")},
        )
        # Commit metadata only after a playable final MP4 exists.
        with LOCK:
            current = _read(folder)
            target = next(c for c in current["clips"] if c["number"] == number)
            history = target.get("revision_history", [])
            history.append({
                key: target.get(key) for key in (
                    "filename", "thumbnail", "start", "duration", "title",
                    "effect", "framing", "captions_added", "voiceover_added",
                    "hook_style", "script", "storyboard", "analysis_basis", "frame_samples",
                    "brand_text",
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
                face_tracking=smart_track is not None,
                narration=revised_narration,
                script=revised_script,
                storyboard=revised_beats,
                quality_report=quality_report,
                boundary_review=[],
                speech_boundary_refined=False,
                analysis_basis=(storyboard["analysis_basis"] if storyboard else "manual"),
                frame_samples=(storyboard["frame_samples"] if storyboard else 0),
                brand_text=edit.brand_text.strip(),
                post_copy=None, post_copy_state=None, post_copy_error=None,
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
        if body.voiceover and (
            not body.voiceover_approved or
            not (body.script.strip() or body.regenerate_storyboard)
        ):
            raise HTTPException(
                400, "Explicit TTS approval and a script or storyboard are required"
            )
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


def recover_interrupted_publications() -> None:
    """Unknown remote status must never be retried automatically."""
    for file in DATA_DIR.glob("*/job.json"):
        try:
            with LOCK:
                job = _read(file.parent)
                changed = False
                for publication in job.get("publications", []):
                    if publication.get("state") in {"uploading", "queued"}:
                        publication.update(
                            state="needs_review",
                            error=("Server restarted while an upload was pending. "
                                   "Check the external platform before retrying; "
                                   "the upload may already have succeeded."),
                        )
                        changed = True
                if changed:
                    _write(file.parent, job)
        except (OSError, ValueError, KeyError):
            LOGGER.warning("Unable to recover publication in job %s", file.parent.name)


def _dispatch_due_publications(now: datetime | None = None) -> int:
    """Atomically claim approved schedules, then submit to one outbound worker."""
    current = now or datetime.now(timezone.utc)
    dispatched = []
    for file in DATA_DIR.glob("*/job.json"):
        folder = file.parent
        try:
            with LOCK:
                job = _read(folder)
                changed = False
                for pub in job.get("publications", []):
                    if pub.get("state") != "scheduled" or pub.get("approved") is not True:
                        continue
                    try:
                        due = datetime.fromisoformat(pub["scheduled_at"])
                        if due.tzinfo is None or due > current:
                            continue
                    except (KeyError, TypeError, ValueError):
                        continue
                    # Mark claimed BEFORE submitting to prevent duplicate queues.
                    pub.update(
                        state="queued", queued_at=current.isoformat(), error=None,
                    )
                    dispatched.append((
                        folder, pub["id"], pub["platform"],
                        folder / "clips" / pub["filename"], pub["title"],
                        pub.get("description", ""), pub.get("visibility", "private"),
                    ))
                    changed = True
                if changed:
                    _write(folder, job)
        except (OSError, ValueError, KeyError):
            LOGGER.warning("Unable to inspect scheduled job %s", folder.name)
    for args in dispatched:
        PUBLISH_POOL.submit(_publish_worker, *args)
    return len(dispatched)


def _publishing_clock() -> None:
    """Low-overhead scheduler: preserves future schedules across restarts."""
    while not PUBLISH_STOP.wait(15):
        try:
            _dispatch_due_publications()
        except Exception:
            LOGGER.exception("Scheduled publishing scan failed")


@app.post("/api/jobs/{job_id}/schedule", status_code=202)
def schedule_clip(job_id: str, body: ScheduledPublishRequest):
    """Explicitly authorize specific stored MP4 for specified future platforms."""
    if not body.approved:
        raise HTTPException(400, "Approve each scheduled external platform transfer")
    if len(set(body.platforms)) != len(body.platforms):
        raise HTTPException(422, "Select each platform at most once")
    now = datetime.now(timezone.utc)
    due = body.scheduled_at
    if due.tzinfo is None:
        raise HTTPException(422, "Scheduled time must include a timezone")
    due = due.astimezone(timezone.utc)
    if not (60 <= (due - now).total_seconds() <= 30 * 86400):
        raise HTTPException(422, "Choose a time 1 minute to 30 days in the future")
    available = {p["id"] for p in platform_status() if p["configured"]}
    if any(p not in available for p in body.platforms):
        raise HTTPException(409, "One or more selected platforms are not configured")
    folder = job_folder(job_id)
    with LOCK:
        job = _read(folder)
        if job.get("status") != "completed":
            raise HTTPException(409, "Complete clip generation before scheduling")
        if body.filename not in {c["filename"] for c in job["clips"]}:
            raise HTTPException(404, "Clip is not the active version in this job")
        if not (folder / "clips" / body.filename).is_file():
            raise HTTPException(404, "Rendered MP4 is missing")
        if len(job.get("publications", [])) + len(body.platforms) > 100:
            raise HTTPException(429, "Publishing history limit reached")
        created = []
        for platform in body.platforms:
            id = uuid.uuid4().hex
            job.setdefault("publications", []).append({
                "id": id, "platform": platform, "filename": body.filename,
                "title": body.title.strip(), "description": body.description,
                "visibility": body.visibility, "state": "scheduled",
                "scheduled_at": due.isoformat(), "approved": True,
                "approved_at": now.isoformat(), "created_at": now.isoformat(),
            })
            created.append(id)
        _write(folder, job)
    return {
        "publication_ids": created, "platforms": body.platforms,
        "state": "scheduled", "scheduled_at": due.isoformat(),
    }


@app.get("/api/publishing/queue")
def scheduled_queue():
    """Persisted metadata only; no credentials or private provider responses."""
    results = []
    for job in list_jobs():
        for pub in job.get("publications", []):
            if pub.get("state") not in {"scheduled", "queued", "uploading", "needs_review"}:
                continue
            results.append({
                "job_id": job["id"], "id": pub["id"],
                "platform": pub["platform"], "filename": pub["filename"],
                "title": pub["title"], "state": pub["state"],
                "scheduled_at": pub.get("scheduled_at"),
            })
    return sorted(results, key=lambda x: x["scheduled_at"] or "")


@app.post("/api/jobs/{job_id}/publications/{pub_id}/cancel")
def cancel_scheduled_publication(job_id: str, pub_id: str):
    folder = job_folder(job_id)
    with LOCK:
        job = _read(folder)
        pub = next((p for p in job.get("publications", []) if p["id"] == pub_id), None)
        if pub is None:
            raise HTTPException(404, "Publication not found")
        if pub.get("state") != "scheduled":
            raise HTTPException(409, "Only a future scheduled publication can be canceled")
        pub.update(state="canceled", canceled_at=datetime.now(timezone.utc).isoformat())
        _write(folder, job)
    return {"id": pub_id, "state": "canceled"}


@app.post("/api/jobs/{job_id}/publications/{pub_id}/reapprove", status_code=202)
def reapprove_publication(job_id: str, pub_id: str, body: ReapproveRequest):
    """Never retry an uncertain provider upload without fresh duplicate-risk consent."""
    if not body.approved or not body.duplicate_checked:
        raise HTTPException(400, "Check the platform and acknowledge possible duplicate posts")
    folder = job_folder(job_id)
    with LOCK:
        job = _read(folder)
        pub = next((p for p in job.get("publications", []) if p["id"] == pub_id), None)
        if pub is None:
            raise HTTPException(404, "Publication not found")
        if pub.get("state") not in {"needs_review", "failed"}:
            raise HTTPException(409, "Only failed or uncertain uploads can be reviewed")
        if not (folder / "clips" / pub["filename"]).is_file():
            raise HTTPException(404, "Saved media no longer exists")
        if pub["platform"] not in {
            p["id"] for p in platform_status() if p["configured"]
        }:
            raise HTTPException(409, "The platform is not configured")
        pub.update(state="queued", error=None,
                   approved_at=datetime.now(timezone.utc).isoformat(),
                   reapproval_count=pub.get("reapproval_count", 0) + 1)
        _write(folder, job)
        args = (
            folder, pub_id, pub["platform"], folder / "clips" / pub["filename"],
            pub["title"], pub.get("description", ""), pub.get("visibility", "private"),
        )
    PUBLISH_POOL.submit(_publish_worker, *args)
    return {"id": pub_id, "state": "queued"}


@app.get("/api/publishing/presets")
def get_publishing_presets():
    data = load_json(DATA_DIR / "publishing-presets.json")
    return data if isinstance(data, list) else []


@app.put("/api/publishing/presets")
def upsert_publishing_preset(preset: PublishPreset):
    """Non-secret text/target presets: never stores account tokens."""
    selected = preset.model_dump()
    selected["platforms"] = list(dict.fromkeys(selected["platforms"]))
    with LOCK:
        data = get_publishing_presets()
        data = [x for x in data if x.get("name") != preset.name]
        if len(data) >= 10:
            raise HTTPException(429, "Maximum 10 publishing presets")
        data.append(selected)
        save_json(DATA_DIR / "publishing-presets.json", data)
    return selected


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


def _analytics_records() -> list[dict]:
    data = load_json(DATA_DIR / "analytics.json")
    return data if isinstance(data, list) else []


@app.post("/api/analytics/records", status_code=201)
def add_analytics_record(entry: AnalyticsEntryRequest):
    """Record a MANUAL metric snapshot, never claim provider-verified data."""
    if entry.platform not in PLATFORMS:
        raise HTTPException(422, "Unknown performance platform")
    if entry.post_url and not valid_https_url(entry.post_url):
        raise HTTPException(422, "Post URL must use HTTPS without credentials")
    if entry.evidence_url and not valid_https_url(entry.evidence_url):
        raise HTTPException(422, "Evidence URL must use HTTPS without credentials")
    if not entry.post_url and not (entry.job_id and entry.filename):
        raise HTTPException(422, "Provide an HTTPS post URL or a saved job and clip")
    if entry.job_id or entry.filename:
        if not entry.job_id or not entry.filename:
            raise HTTPException(422, "A job ID and filename must be supplied together")
        folder = job_folder(entry.job_id)
        job = _read(folder)
        valid_files = {clip["filename"] for clip in job.get("clips", [])}
        for clip in job.get("clips", []):
            valid_files.update(
                old.get("filename") for old in clip.get("revision_history", [])
            )
        if entry.filename not in valid_files:
            raise HTTPException(404, "Clip does not belong to this job")
    row = {
        **entry.model_dump(exclude={"earnings_usd"}),
        "earnings_usd": f"{entry.earnings_usd:.2f}",
        "id": uuid.uuid4().hex,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": "self_reported_manual",
        "post_key": post_key(
            entry.platform, entry.post_url, entry.job_id, entry.filename,
        ),
    }
    with LOCK:
        records = _analytics_records()
        if len(records) >= 5000:
            raise HTTPException(429, "Maximum 5,000 metric snapshots; export before archiving")
        records.append(row)
        save_json(DATA_DIR / "analytics.json", records)
    return row


@app.get("/api/analytics/records")
def analytics_records():
    return _analytics_records()[-200:][::-1]


@app.get("/api/analytics/summary")
def analytics_summary():
    publication_states = {}
    for file in DATA_DIR.glob("*/job.json"):
        try:
            job = _read(file.parent)
            for pub in job.get("publications", []):
                status = pub.get("state", "unknown")
                publication_states[status] = publication_states.get(status, 0) + 1
        except (OSError, ValueError, KeyError):
            continue
    return summarize(_analytics_records(), publication_states)


@app.get("/api/analytics/export")
def export_analytics():
    """Export all manually entered snapshots, with CSV formula safeguards."""
    text = safe_csv(_analytics_records())
    return Response(
        content=text.encode("utf-8-sig"), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="clipforge-analytics.csv"'},
    )


@app.delete("/api/analytics/records/{record_id}", status_code=204)
def delete_analytics_record(record_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", record_id):
        raise HTTPException(404, "Metric snapshot not found")
    with LOCK:
        rows = _analytics_records()
        kept = [row for row in rows if row.get("id") != record_id]
        if len(kept) == len(rows):
            raise HTTPException(404, "Metric snapshot not found")
        save_json(DATA_DIR / "analytics.json", kept)
    return Response(status_code=204)


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
