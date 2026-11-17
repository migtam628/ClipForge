"""Bounded, source-grounded scene-cut inspection for the timeline editor.

Detects visual transitions using FFmpeg frame differences and attaches only
verbatim transcript excerpts. This is *not* semantic object/event recognition:
users must review proposed cuts and the actual footage before exporting.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

from clipforge.recovery import load_json, save_json

VERSION = 1
MAX_SCENES = 12
_TIMESTAMP = re.compile(r"\bpts_time:\s*([+-]?\d+(?:\.\d+)?)\b")


def detect_scene_cuts(source: Path, start: float, duration: float,
                      *, threshold: float = 0.28) -> list[float]:
    """Return absolute source times for clear shot changes, max 11 boundaries.

    Detection runs only against the selected 3–120s range, not an entire
    multi-hour source. Scaling and frame decimation bound intermediate memory.
    """
    if not source.is_file():
        raise FileNotFoundError("Original source is unavailable")
    if not (math.isfinite(start) and math.isfinite(duration)
            and 0 <= start and 3 <= duration <= 120):
        raise ValueError("Scene analysis requires a valid 3–120 second range")
    if not math.isfinite(threshold) or not 0.1 <= threshold <= 0.9:
        raise ValueError("Invalid visual scene threshold")
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info", "-ss",
        f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
        "-an", "-vf",
        f"scale=160:-2,fps=3,select=gt(scene\\,{threshold:.2f}),showinfo",
        "-fps_mode", "vfr", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            timeout=min(180, max(25, math.ceil(duration * 2.5))), check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Scene detection timed out; shorten the selected range") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Unable to detect cuts in this source video") from exc

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


def inspect_scene_range(source: Path, transcript: list[dict], start: float,
                        duration: float, checkpoint: Path) -> dict:
    """Cache visual boundaries across repeated editor requests for one source."""
    stat = source.stat()
    digest = hashlib.sha256(json.dumps([
        VERSION, str(source.resolve()), stat.st_size, stat.st_mtime_ns,
        round(start, 3), round(duration, 3),
    ]).encode()).hexdigest()
    saved = load_json(checkpoint)
    if isinstance(saved, dict) and saved.get("signature") == digest:
        cuts = saved.get("cuts")
        if (isinstance(cuts, list) and len(cuts) <= MAX_SCENES - 1
                and all(type(t) is float and start < t < start + duration
                        for t in cuts)):
            return {"cuts": cuts, "scenes": scene_evidence(transcript, start, duration, cuts),
                    "method": "frame_difference", "cached": True}
    cuts = detect_scene_cuts(source, start, duration)
    save_json(checkpoint, {"signature": digest, "cuts": cuts})
    return {"cuts": cuts, "scenes": scene_evidence(transcript, start, duration, cuts),
            "method": "frame_difference", "cached": False}
