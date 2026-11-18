"""Conservative, review-only editorial recommendations for source video ranges.

Speech evidence is source-aligned; sampled visual descriptions are *unverified*
local model drafts, not facts. A recommendation never edits or writes media and
never grants consent to generate paid narration.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from clipforge.scene_analysis import _visual_draft, inspect_scene_range
from clipforge.storyboard import _meaningful_tokens, _transcript_evidence

MAX_SAMPLES = 3


def _source_timestamp(segments: list[dict], output_time: float) -> float:
    """Map a timestamp on an ordered edit timeline to the original source."""
    cursor = 0.0
    for index, segment in enumerate(segments):
        span = float(segment["duration"])
        if output_time < cursor + span or index == len(segments) - 1:
            # FFmpeg cannot decode an exclusive end position reliably.
            offset = max(0.0, min(output_time - cursor, max(0.0, span - 0.05)))
            return round(float(segment["start"]) + offset, 3)
        cursor += span
    raise ValueError("An ordered edit requires at least one source segment")


def _sequence_transcript(transcript: list[dict], segments: list[dict]) -> list[dict]:
    """Project source speech into output order without fabricating word timings.

    Entire source ASR phrases are retained when they overlap a cut. A phrase
    crossing a boundary can be duplicated; the review explicitly marks the
    mapping as approximate rather than claiming word-perfect alignment.
    """
    output = []
    cursor = 0.0
    for segment in segments:
        begin = float(segment["start"])
        length = float(segment["duration"])
        end = begin + length
        for phrase in transcript:
            if not isinstance(phrase, dict) or not isinstance(phrase.get("text"), str):
                continue
            try:
                left, right = float(phrase["start"]), float(phrase["end"])
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
            if not (math.isfinite(left) and math.isfinite(right) and left < right
                    and left < end and right > begin):
                continue
            output.append({
                "start": round(cursor + max(begin, left) - begin, 3),
                "end": round(cursor + min(end, right) - begin, 3),
                "text": phrase["text"],
            })
        cursor += length
    return output


def _selection_transcript(transcript: list[dict], segments: list[dict] | None) -> list[dict]:
    return _sequence_transcript(transcript, segments) if segments else transcript


def _sample_source_time(segments: list[dict] | None, moment: float) -> float:
    return _source_timestamp(segments, moment) if segments else moment


def _source_speech(transcript: list[dict], start: float, duration: float) -> tuple[int, float]:
    """Count overlapping source words and unioned timed speech coverage."""
    end = start + duration
    intervals: list[tuple[float, float]] = []
    word_count = 0
    for entry in transcript:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        try:
            left, right = float(entry["start"]), float(entry["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not (math.isfinite(left) and math.isfinite(right) and left < right and
                left < end and right > start):
            continue
        word_count += len(re.findall(r"\b\w+\b", entry["text"], re.UNICODE))
        intervals.append((max(start, left), min(end, right)))
    intervals.sort()
    merged: list[list[float]] = []
    for left, right in intervals:
        if merged and left <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return word_count, round(sum(right - left for left, right in merged) / duration, 3)


def recommend_content_mode(transcript: list[dict], start: float, duration: float,
                           evidence: list[dict], *, visual_status: str) -> dict:
    """Make a guarded editorial choice from *observations*, not imagined scenes."""
    words, coverage = _source_speech(transcript, start, duration)
    visual = [item for item in evidence if item.get("visual_observation")]
    supported_context = sum(
        len(_meaningful_tokens(str(item.get("speech_excerpt", ""))) &
            _meaningful_tokens(str(item.get("visual_observation", "")))) >= 2
        for item in visual
    )
    visual_review = bool(visual)
    if words >= 12 or coverage >= 0.22:
        mode, reason = ("captions_only", "Original dialogue carries the story; preserve "
                        "it and use captions without invented commentary.")
    elif words >= 3 and supported_context >= 1 and len(visual) >= 2:
        mode, reason = ("context_labels", "Short speech and sampled visuals overlap "
                        "on concrete words. Optional labels may help, but require review.")
    elif words >= 3:
        mode, reason = ("captions_only", "There is some source dialogue but too little "
                        "corroborated visual context to recommend extra writing.")
    else:
        mode, reason = ("no_extra_text", "No reliable speech-based explanation was found; "
                        "keep the original footage unobstructed by default.")
    story_option = len(visual) == MAX_SAMPLES and len({
        str(item["visual_observation"]).casefold().strip() for item in visual
    }) >= 2
    return {
        "suggested_mode": mode,
        "reason": reason,
        "review_required": True,
        "confidence": "limited" if not visual_review else "source_supported_limited",
        "analysis_basis": "source_speech_and_sampled_ai_frames" if visual_review
        else "source_speech_only",
        "visual_status": visual_status,
        "sampled_frames": len(visual),
        "speech_words": words,
        "speech_coverage": coverage,
        "context_corroborated_windows": supported_context,
        "optional_story_script": story_option,
        "evidence": evidence,
        "warning": ("AI visual descriptions represent single frames and are unverified. "
                    "Check the original video, dialogue, and sequence before applying suggestions. "
                    "No narration or rendering has been started."),
    }


def analyze_content_range(source: Path, transcript: list[dict], start: float,
                          duration: float, checkpoint: Path, *,
                          use_vision: bool, progress=None,
                          segments: list[dict] | None = None) -> dict:
    """Review three bounded source windows; cache text only, never images."""
    if not source.is_file() or not math.isfinite(start) or not math.isfinite(duration):
        raise ValueError("A readable original source and finite range are required")
    if start < 0 or not 3 <= duration <= 120:
        raise ValueError("Select 3–120 seconds inside the original source")
    working_transcript = _selection_transcript(transcript, segments)
    timeline_start = 0.0 if segments else start
    entries = []
    failures = 0
    for index in range(MAX_SAMPLES):
        left = timeline_start + duration * index / MAX_SAMPLES
        span = duration / MAX_SAMPLES
        timeline_at = left + span / 2
        source_at = _sample_source_time(segments, timeline_at)
        observation = ""
        if use_vision:
            try:
                sample_range = ({"start": source_at, "duration": 0} if segments else
                                {"start": left, "duration": span})
                draft = _visual_draft(
                    source, sample_range,
                    checkpoint.with_name(checkpoint.stem + f"-frame-{index}.json"),
                )
                observation = draft["observation"]
            except (RuntimeError, OSError, ValueError):
                failures += 1  # Speech evidence remains valid if vision fails.
        entries.append({
            "timestamp": round(timeline_at, 2),
            "source_timestamp": round(source_at, 2),
            "start": round(left, 2), "end": round(left + span, 2),
            "speech_excerpt": _transcript_evidence(
                working_transcript, timeline_start, left - timeline_start,
                left - timeline_start + span,
            )[:240],
            "visual_observation": observation,
            "visual_basis": "one_unverified_ai_frame" if observation else "unavailable",
        })
        if progress:
            progress(15 + round(75 * (index + 1) / MAX_SAMPLES),
                     f"Reviewed source window {index + 1}/{MAX_SAMPLES}")
    status = ("ready" if use_vision and not failures else
              "partial" if use_vision and failures < MAX_SAMPLES else
              "unavailable" if use_vision else "not_requested")
    result = recommend_content_mode(working_transcript, timeline_start, duration,
                                    entries, visual_status=status)
    if segments:
        result.update(analysis_context="ordered_sequence", segment_count=len(segments),
                      transcript_timing="source_segment_approximate")
    return result


def _compare_descriptions(before: str, after: str) -> str:
    """Only compare words in two unverified snapshots; do not infer motion."""
    if not before or not after:
        return "insufficient_visual_evidence"
    earlier = _meaningful_tokens(before)
    later = _meaningful_tokens(after)
    if not earlier or not later:
        return "insufficient_visual_evidence"
    overlap = len(earlier & later) / len(earlier | later)
    if overlap >= 0.65:
        return "similar_descriptions"
    if overlap <= 0.25:
        return "different_descriptions"
    return "uncertain_difference"


def analyze_temporal_range(source: Path, transcript: list[dict], start: float,
                           duration: float, checkpoint: Path, *,
                           use_vision: bool, progress=None,
                           segments: list[dict] | None = None) -> dict:
    """Sample *pairs* of frames across a clip and align coarse shot evidence.

    This analyzes before/after snapshots within consecutive source windows.
    Differences can suggest edits, but neither changing pixels nor changing
    descriptions prove that a particular object moved or action occurred.
    """
    if not source.is_file() or not math.isfinite(start) or not math.isfinite(duration):
        raise ValueError("A readable original source and finite range are required")
    if start < 0 or not 3 <= duration <= 120:
        raise ValueError("Select 3–120 seconds inside the original source")

    def scan_progress(percent: int, stage: str) -> None:
        if progress:
            progress(10 + round(percent * 0.25), stage)

    working_transcript = _selection_transcript(transcript, segments)
    timeline_start = 0.0 if segments else start
    if segments:
        # These are real user-arranged edit boundaries, not inferred shot cuts.
        # Do not scan unrelated original footage or propose unmappable trims.
        junctions = []
        cursor = 0.0
        for segment in segments[:-1]:
            cursor += float(segment["duration"])
            junctions.append(round(cursor, 2))
        scan = {
            "cuts": junctions, "method": "existing_edit_boundaries",
            "analysis_status": "known_edits",
            "warning": "These are existing timeline joins, not newly detected scene changes.",
            "auto_edit": {"segments": [], "removed_seconds": 0,
                          "reason": "ordered_sequence_requires_manual_pacing",
                          "review_required": True},
        }
    else:
        try:
            scan = inspect_scene_range(
                source, transcript, start, duration,
                checkpoint.with_name(checkpoint.stem + "-scene-cuts.json"),
                progress=scan_progress,
            )
        except (RuntimeError, TimeoutError, OSError):
            # A failed source scan does not invalidate existing speech.
            scan = {
                "cuts": [], "method": "unavailable", "analysis_status": "partial",
                "warning": "Visual shot scanning was unavailable; no cut list was produced.",
                "auto_edit": {"segments": [], "removed_seconds": 0,
                              "reason": "scene_scan_unavailable", "review_required": True},
            }
    windows = []
    evidence = []
    sample_count = 0
    failures = 0
    count = 2 if duration < 12 else MAX_SAMPLES
    for index in range(count):
        left = timeline_start + duration * index / count
        span = duration / count
        before_at, after_at = left + span * 0.5, left + span * 0.85
        before_source = _sample_source_time(segments, before_at)
        after_source = _sample_source_time(segments, after_at)
        before, after = "", ""
        if use_vision:
            for phase, scene, cache_path in (
                ("before", ({"start": before_source, "duration": 0} if segments else
                            {"start": left, "duration": span}),
                 checkpoint.with_name(checkpoint.stem + f"-frame-{index}.json")),
                ("after", ({"start": after_source, "duration": 0} if segments else
                           {"start": left + span * 0.7, "duration": span * 0.3}),
                 checkpoint.with_name(checkpoint.stem + f"-sequence-after-{index}.json")),
            ):
                try:
                    draft = _visual_draft(source, scene, cache_path)
                except (RuntimeError, OSError, ValueError):
                    failures += 1
                else:
                    if phase == "before":
                        before = draft["observation"]
                    else:
                        after = draft["observation"]
                    sample_count += 1
        speech = _transcript_evidence(
            working_transcript, timeline_start,
            left - timeline_start, left - timeline_start + span,
        )[:240]
        signal = _compare_descriptions(before, after)
        windows.append({
            "start": round(left, 2), "end": round(left + span, 2),
            "before": {"timestamp": round(before_at, 2),
                       "source_timestamp": round(before_source, 2), "observation": before},
            "after": {"timestamp": round(after_at, 2),
                      "source_timestamp": round(after_source, 2), "observation": after},
            "speech_excerpt": speech, "change_signal": signal,
            "review_required": True,
        })
        evidence.append({
            "timestamp": round(before_at, 2), "start": round(left, 2),
            "source_timestamp": round(before_source, 2),
            "end": round(left + span, 2), "speech_excerpt": speech,
            "visual_observation": " ".join(v for v in (before, after) if v),
            "visual_basis": "two_unverified_ai_frames" if before and after
            else "partial_unverified_ai_frames" if before or after else "unavailable",
        })
        if progress:
            progress(35 + round(55 * (index + 1) / count),
                     f"Comparing source window {index + 1}/{count}")
    status = ("ready" if use_vision and failures == 0 else
              "partial" if use_vision and sample_count else
              "unavailable" if use_vision else "not_requested")
    result = recommend_content_mode(working_transcript, timeline_start, duration, evidence,
                                    visual_status=status)
    # The base planner counts described windows; this report includes individual
    # snapshots and reports the actual number, even when some inference fails.
    result["sampled_frames"] = sample_count
    result["max_frames"] = count * 2
    result["analysis_basis"] = ("source_speech_and_temporal_frame_pairs" if sample_count
                                else "source_speech_and_shot_boundaries_only")
    result["analysis_depth"] = "sequence"
    if segments:
        result.update(analysis_context="ordered_sequence", segment_count=len(segments),
                      transcript_timing="source_segment_approximate")
    pacing = scan["auto_edit"]
    if scan["analysis_status"] == "partial":
        pacing = {"segments": [], "removed_seconds": 0,
                  "reason": "incomplete_scene_scan", "review_required": True}
    result["temporal"] = {
        "windows": windows,
        "cuts": scan["cuts"],
        "cut_points": [{"timestamp": point,
                        "source_timestamp": _sample_source_time(segments, point)}
                       for point in scan["cuts"]],
        "cut_method": scan["method"],
        "cut_analysis_status": scan["analysis_status"],
        "cut_warning": scan.get("warning"),
        "pacing": pacing,
        "review_required": True,
        "interpretation": ("Different visual descriptions identify possible changes "
                           "between sampled moments, not verified actions, object tracking, "
                           "or continuous coverage. Review source footage and all cut points."),
    }
    return result
