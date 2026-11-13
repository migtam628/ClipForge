"""ClipForge AI Engine 2.0: conservative boundary editing and real media QA.

The renderer's technical integrity can be tested automatically. Whether a
narration is factually true, compelling or monetizable CANNOT be established by
container metadata, so this module never marks output "publish-ready" by itself.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path


def refine_speech_boundaries(
    candidates: list[dict], transcript: list[dict], source_duration: float,
    *, tolerance: float = 3.0,
) -> list[dict]:
    """Avoid cutting through recognized speech segments when safe.

    Only a segment *crossing* an existing edit point can move that boundary.
    Never infer a new highlight, extend beyond the source, or create overlaps.
    Long utterances which cannot be included safely are flagged for review.
    """
    if not math.isfinite(source_duration) or source_duration < 0:
        raise ValueError("Invalid source duration")
    segments: list[tuple[float, float]] = []
    for item in transcript:
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (math.isfinite(start) and math.isfinite(end)
                and 0 <= start < end <= source_duration + 0.1):
            segments.append((max(0.0, start), min(source_duration, end)))
    segments.sort()
    indexed = sorted(enumerate(candidates), key=lambda it: float(it[1]["start"]))
    result: dict[int, dict] = {}
    previous_end = 0.0
    for position, (index, candidate) in enumerate(indexed):
        original_start = float(candidate["start"])
        original_end = original_start + float(candidate["duration"])
        if not (math.isfinite(original_start) and math.isfinite(original_end)
                and 0 <= original_start < original_end <= source_duration + 0.1):
            raise ValueError("Selected highlight lies outside source footage")
        next_start = (
            float(indexed[position + 1][1]["start"])
            if position + 1 < len(indexed) else source_duration
        )
        start, end = original_start, min(source_duration, original_end)
        warnings: list[str] = []
        crossing_start = [segment for segment in segments
                          if segment[0] + 0.05 < original_start < segment[1] - 0.05]
        crossing_end = [segment for segment in segments
                        if segment[0] + 0.05 < original_end < segment[1] - 0.05]
        if crossing_start:
            nearest = max(crossing_start, key=lambda x: x[0])
            shift = original_start - nearest[0]
            if shift <= tolerance and nearest[0] >= previous_end - 0.001:
                start = nearest[0]
            else:
                warnings.append("possible_mid_sentence_start")
        if crossing_end:
            nearest = min(crossing_end, key=lambda x: x[1])
            shift = nearest[1] - original_end
            if (shift <= tolerance and nearest[1] <= source_duration + 0.001
                    and nearest[1] <= next_start + 0.001
                    and nearest[1] - start <= 120):
                end = nearest[1]
            else:
                warnings.append("possible_mid_sentence_end")
        # Keep selection recognizable; do not produce pathological short clips.
        if end - start < 3 or end - start < (original_end - original_start) * 0.7:
            start, end = original_start, original_end
            warnings.append("boundary_adjustment_unavailable")
        if start < previous_end - 0.001:
            start = original_start
            warnings.append("adjacent_clip_overlap_prevented")
        previous_end = end
        final = dict(candidate)
        final["start"] = round(start, 2)
        final["duration"] = round(end - start, 2)
        final["boundary_review"] = sorted(set(warnings))
        final["speech_boundary_refined"] = (
            abs(start - original_start) > 0.01 or abs(end - original_end) > 0.01
        )
        result[index] = final
    return [result[i] for i in range(len(candidates))]


def _media_inspection(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size <= 500:
        raise ValueError("Rendered video is missing or empty")
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=40,
    )
    data = json.loads(completed.stdout)
    streams = data.get("streams", [])
    videos = [entry for entry in streams if entry.get("codec_type") == "video"]
    audios = [entry for entry in streams if entry.get("codec_type") == "audio"]
    if not videos:
        raise ValueError("Rendered file has no video track")
    video = videos[0]
    duration = float(data.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Rendered file duration is invalid")
    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": bool(audios),
    }


def inspect_rendered_clip(
    path: Path, *, expected_duration: float, aspect_ratio: str,
    source_has_audio: bool, narration: str, script: str | None,
    beats: list[dict] | None, captions_requested: bool, captions_added: bool,
    candidate: dict,
) -> dict:
    """Machine-testable export checks and conservative editorial warnings.

    This reports only observable problems; a passing technical audit does NOT
    establish narrative truth, creative quality, rights or platform suitability.
    """
    fatal: list[str] = []
    review: list[str] = []
    metrics: dict = {}
    try:
        media = _media_inspection(path)
        metrics = {
            "duration_seconds": round(media["duration"], 2),
            "width": media["width"], "height": media["height"],
            "audio_present": media["has_audio"],
        }
        if abs(media["duration"] - expected_duration) > 1.25:
            fatal.append("render_duration_mismatch")
        desired = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720)}
        if aspect_ratio not in desired or (
            media["width"], media["height"]
        ) != desired.get(aspect_ratio):
            fatal.append("render_resolution_mismatch")
        if (source_has_audio or narration == "narrated") and not media["has_audio"]:
            fatal.append("expected_audio_missing")
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        fatal.append("media_unreadable")
    if captions_requested and not captions_added:
        review.append("requested_captions_unavailable")
    if narration != "none":
        if not script or not script.strip() or not beats:
            review.append("missing_narration_timeline")
        else:
            cleaned = [" ".join(re.findall(r"\w+", str(beat.get("text", "")).lower()))
                       for beat in beats]
            # Repeated full narration beats are likely to sound robotic.
            nonempty = [text for text in cleaned if len(text.split()) >= 4]
            if len(nonempty) != len(set(nonempty)):
                review.append("repeated_narration_beats")
            if any(
                len(str(beat.get("text", "")).split())
                > max(3, int(max(0, float(beat.get("end", 0)) -
                                  float(beat.get("start", 0))) * 2))
                for beat in beats
            ):
                review.append("narration_over_word_budget")
    review.extend(candidate.get("boundary_review") or [])
    score = candidate.get("score")
    if isinstance(score, (float, int)) and not isinstance(score, bool) and score < 6:
        review.append("weak_highlight_score")
    if narration != "none" and candidate.get("creator_niche") in {"sports", "news"}:
        review.append("factual_claims_need_source_verification")
    # Human or stronger multimodal QA is required before claiming publish-ready.
    review.append("editorial_and_rights_review_not_automated")
    return {
        "version": 1,
        "technical_pass": not fatal,
        "status": "technical_issue" if fatal else "review_required",
        "technical_issues": sorted(set(fatal)),
        "review_flags": sorted(set(review)),
        "metrics": metrics,
        "publish_ready_verified": False,
    }
