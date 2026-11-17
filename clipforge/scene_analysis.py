"""Bounded, source-grounded scene-cut inspection for the timeline editor.

Detects visual transitions using FFmpeg frame differences and attaches only
verbatim transcript excerpts. This is *not* semantic object/event recognition:
users must review proposed cuts and the actual footage before exporting.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import subprocess
from pathlib import Path

from clipforge.recovery import load_json, save_json

VERSION = 3
MAX_SCENES = 12
_TIMESTAMP = re.compile(r"\bpts_time:\s*([+-]?\d+(?:\.\d+)?)\b")
LOGGER = logging.getLogger("uvicorn.error")


def _validate_range(source: Path, start: float, duration: float, threshold: float) -> None:
    if not source.is_file():
        raise FileNotFoundError("Original source is unavailable")
    if not (math.isfinite(start) and math.isfinite(duration)
            and 0 <= start and 3 <= duration <= 120):
        raise ValueError("Scene analysis requires a valid 3–120 second range")
    if not math.isfinite(threshold) or not 0.1 <= threshold <= 0.9:
        raise ValueError("Invalid visual scene threshold")


def _scan_frames(source: Path, start: float, duration: float, threshold: float,
                 *, keyframes_only: bool, timeout: int) -> list[float]:
    """Use input seeking and at most two decode threads on constrained hosts."""
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info",
        "-filter_threads", "1", "-threads", "2",
    ]
    if keyframes_only:
        command.extend(["-skip_frame", "nokey"])
    command.extend([
        "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
        "-an", "-vf", (
            f"scale=160:-2,select=gt(scene\\,{threshold:.2f}),showinfo"
            if keyframes_only else
            f"fps=2,scale=160:-2,select=gt(scene\\,{threshold:.2f}),showinfo"
        ),
        "-fps_mode", "vfr", "-f", "null", "-",
    ])
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=True,
        )
    except subprocess.TimeoutExpired as exc:
        LOGGER.warning("Scene detection %s pass exceeded %ss for %.1fs selection",
                       "keyframe" if keyframes_only else "sampled-frame", timeout, duration)
        raise TimeoutError("Visual scan exceeded its CPU time budget") from exc
    except subprocess.CalledProcessError as exc:
        LOGGER.warning("Scene detection %s pass could not decode source (FFmpeg exit %s)",
                       "keyframe" if keyframes_only else "sampled-frame", exc.returncode)
        raise RuntimeError("Unable to decode video for scene detection") from exc

    found: list[float] = []
    for line in result.stderr.splitlines():
        if "showinfo" not in line or "pts_time:" not in line:
            continue
        match = _TIMESTAMP.search(line)
        if not match:
            continue
        relative = float(match.group(1))
        if (not math.isfinite(relative) or relative < 0.75
                or relative > duration - 0.75):
            continue
        cut = round(start + relative, 2)
        if found and cut - found[-1] < 1.0:
            continue
        found.append(cut)
        if len(found) == MAX_SCENES - 1:
            break
    return found


def _detect_scene_details(source: Path, start: float, duration: float,
                          *, threshold: float = 0.28, progress=None) -> dict:
    """Try cheap keyframe analysis first; only do a full scan when needed.

    Neither pass runs without a deadline. A timed-out detailed pass returns an
    explicitly incomplete review result, not a misleading 'zero scene cuts'.
    """
    _validate_range(source, start, duration, threshold)
    fast_timeout = min(35, max(10, math.ceil(duration * 0.8)))
    detail_timeout = min(60, max(12, math.ceil(duration * 1.2)))
    fast_cuts: list[float] = []
    fast_error: Exception | None = None
    if progress:
        progress(20, "Scanning video keyframes")
    try:
        fast_cuts = _scan_frames(source, start, duration, threshold,
                                keyframes_only=True, timeout=fast_timeout)
    except (TimeoutError, RuntimeError) as exc:
        fast_error = exc
    if fast_cuts:
        return {
            "cuts": fast_cuts, "method": "keyframe_difference", "analysis_status": "coarse",
            "warning": "Keyframe-level cuts are approximate; review footage for transitions between keyframes.",
        }

    if progress:
        progress(60, "Checking additional sampled frames")
    try:
        detailed_cuts = _scan_frames(source, start, duration, threshold,
                                    keyframes_only=False, timeout=detail_timeout)
    except TimeoutError:
        return {
            "cuts": [], "method": "bounded_frame_difference", "analysis_status": "partial",
            "warning": "Scene scan reached its CPU time limit; no reliable cut list was produced. "
                       "Try a shorter selection or split manually.",
        }
    except RuntimeError:
        if fast_error is not None:
            raise RuntimeError("Both keyframe and sampled-frame scans could not decode this video")
        raise
    return {"cuts": detailed_cuts, "method": "sampled_frame_difference",
            "analysis_status": "complete", "warning": None}


def detect_scene_cuts(source: Path, start: float, duration: float,
                      *, threshold: float = 0.28) -> list[float]:
    """Return up to eleven reviewable shot boundaries within the selected range."""
    return _detect_scene_details(source, start, duration, threshold=threshold)["cuts"]


def scene_evidence(transcript: list[dict], start: float, duration: float,
                   cuts: list[float]) -> list[dict]:
    """Create timeline scene cards from boundaries and observed spoken words."""
    boundaries = [start, *cuts, start + duration]
    result: list[dict] = []
    for left, right in zip(boundaries, boundaries[1:]):
        speech = []
        for segment in transcript:
            if not isinstance(segment, dict):
                continue
            try:
                began = float(segment["start"])
                ended = float(segment["end"])
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            if (math.isfinite(began) and math.isfinite(ended)
                    and began < right and ended > left):
                phrase = segment.get("text")
                if isinstance(phrase, str):
                    speech.append(phrase.strip())
        result.append({
            "start": round(left, 2), "duration": round(right - left, 2),
            "speech_excerpt": " ".join(speech)[:200],
            "evidence": "visual_transition_and_source_transcript",
        })
    return result


def suggest_speech_pacing(transcript: list[dict], start: float,
                          duration: float) -> dict:
    """Suggest deleting only large internal transcript gaps, never source content.

    This is a pacing heuristic, *not* a semantic decision: silence may contain
    action, music or reactions. Suggestions must be reviewed in the timeline.
    The beginning and end of the selected video are deliberately preserved.
    """
    end = start + duration
    intervals: list[tuple[float, float]] = []
    for segment in transcript:
        if not isinstance(segment, dict) or not str(segment.get("text") or "").strip():
            continue
        try:
            left, right = float(segment["start"]), float(segment["end"])
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        if all(map(math.isfinite, (left, right))) and left < right and right > start and left < end:
            intervals.append((max(start, left), min(end, right)))
    intervals.sort()
    if len(intervals) < 2:
        return {"segments": [], "removed_seconds": 0, "reason": "insufficient_speech_evidence",
                "review_required": True}
    speech: list[list[float]] = []
    for left, right in intervals:
        if speech and left <= speech[-1][1]:
            speech[-1][1] = max(speech[-1][1], right)
        else:
            speech.append([left, right])
    # The longest pauses are considered first to respect the 12-piece editor cap.
    pauses = []
    for previous, following in zip(speech, speech[1:]):
        gap_start, gap_end = previous[1] + 0.5, following[0] - 0.5
        if gap_end - gap_start >= 2.5:
            pauses.append((gap_start, gap_end))
    if not pauses:
        return {"segments": [], "removed_seconds": 0, "reason": "no_long_internal_pauses",
                "review_required": True}
    pauses = sorted(sorted(pauses, key=lambda p: p[1] - p[0], reverse=True)[:11])
    segments = []
    cursor = start
    for gap_start, gap_end in pauses:
        if gap_start - cursor >= 0.5:
            segments.append({"start": round(cursor, 2),
                             "duration": round(gap_start - cursor, 2)})
            cursor = gap_end
    if end - cursor >= 0.5:
        segments.append({"start": round(cursor, 2),
                         "duration": round(end - cursor, 2)})
    removed = round(duration - sum(s["duration"] for s in segments), 2)
    if not segments or removed < 2 or sum(s["duration"] for s in segments) < 3:
        return {"segments": [], "removed_seconds": 0, "reason": "no_safe_pacing_suggestion",
                "review_required": True}
    return {"segments": segments, "removed_seconds": removed,
            "reason": "long_gaps_between_transcribed_speech", "review_required": True,
            "warning": "Silent sections can contain important visual action; preview every cut."}


def _visual_draft(source: Path, scene: dict, checkpoint: Path) -> dict:
    """One optional, cached frame observation; never a verified scene summary."""
    from clipforge.storyboard import _visual_evidence

    timestamp = scene["start"] + scene["duration"] * 0.5
    stat = source.stat()
    signature = hashlib.sha256(json.dumps([
        VERSION, str(source.resolve()), stat.st_size, stat.st_mtime_ns,
        os.environ.get("CLIPFORGE_VISION_MODEL", "moondream"), round(timestamp, 3),
    ]).encode()).hexdigest()
    stored = load_json(checkpoint)
    if isinstance(stored, dict) and stored.get("signature") == signature:
        observation = stored.get("observation")
        if isinstance(observation, str) and observation.strip() and len(observation) <= 400:
            return {"observation": observation, "timestamp": round(timestamp, 2),
                    "basis": "one_ai_described_video_frame", "review_required": True,
                    "cached": True}
    observation = _visual_evidence(source, timestamp)
    save_json(checkpoint, {"signature": signature, "observation": observation})
    return {"observation": observation, "timestamp": round(timestamp, 2),
            "basis": "one_ai_described_video_frame", "review_required": True,
            "cached": False}


def inspect_scene_range(source: Path, transcript: list[dict], start: float,
                        duration: float, checkpoint: Path,
                        *, vision_scene: int | None = None, progress=None) -> dict:
    """Cache visual boundaries across repeated editor requests for one source."""
    stat = source.stat()
    digest = hashlib.sha256(json.dumps([
        VERSION, str(source.resolve()), stat.st_size, stat.st_mtime_ns,
        round(start, 3), round(duration, 3),
    ]).encode()).hexdigest()
    saved = load_json(checkpoint)
    cached = False
    cuts = None
    details = {"method": "sampled_frame_difference", "analysis_status": "complete", "warning": None}
    if isinstance(saved, dict) and saved.get("signature") == digest:
        cuts = saved.get("cuts")
        if (isinstance(cuts, list) and len(cuts) <= MAX_SCENES - 1
                and all(type(t) is float and start < t < start + duration
                        for t in cuts)):
            cached = True
            details = {"method": saved.get("method", "sampled_frame_difference"),
                       "analysis_status": saved.get("analysis_status", "complete"),
                       "warning": saved.get("warning")}
    if not cached:
        details = _detect_scene_details(source, start, duration, progress=progress)
        cuts = details["cuts"]
        if details["analysis_status"] != "partial":
            save_json(checkpoint, {"signature": digest, **details})
    if progress:
        progress(90, "Preparing scene evidence and pacing suggestions")
    scenes = scene_evidence(transcript, start, duration, cuts)
    result = {"cuts": cuts, "scenes": scenes, **details,
              "cached": cached, "auto_edit": suggest_speech_pacing(transcript, start, duration)}
    if vision_scene is not None:
        if not 0 <= vision_scene < len(scenes):
            raise ValueError("Scene index is outside the detected scene range")
        result["visual_draft"] = _visual_draft(
            source, scenes[vision_scene],
            checkpoint.with_name(checkpoint.stem + f"-frame-{vision_scene}.json"),
        )
    return result
