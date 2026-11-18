"""Conservative, review-only editorial recommendations for source video ranges.

Speech evidence is source-aligned; sampled visual descriptions are *unverified*
local model drafts, not facts. A recommendation never edits or writes media and
never grants consent to generate paid narration.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from clipforge.scene_analysis import _visual_draft
from clipforge.storyboard import _meaningful_tokens, _transcript_evidence

MAX_SAMPLES = 3


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
                          use_vision: bool, progress=None) -> dict:
    """Review three bounded source windows; cache text only, never images."""
    if not source.is_file() or not math.isfinite(start) or not math.isfinite(duration):
        raise ValueError("A readable original source and finite range are required")
    if start < 0 or not 3 <= duration <= 120:
        raise ValueError("Select 3–120 seconds inside the original source")
    entries = []
    failures = 0
    for index in range(MAX_SAMPLES):
        left = start + duration * index / MAX_SAMPLES
        span = duration / MAX_SAMPLES
        observation = ""
        if use_vision:
            try:
                draft = _visual_draft(
                    source, {"start": left, "duration": span},
                    checkpoint.with_name(checkpoint.stem + f"-frame-{index}.json"),
                )
                observation = draft["observation"]
            except (RuntimeError, OSError, ValueError):
                failures += 1  # Speech evidence remains valid if vision fails.
        entries.append({
            "timestamp": round(left + span / 2, 2),
            "start": round(left, 2), "end": round(left + span, 2),
            "speech_excerpt": _transcript_evidence(transcript, start, left - start,
                                                    left - start + span)[:240],
            "visual_observation": observation,
            "visual_basis": "one_unverified_ai_frame" if observation else "unavailable",
        })
        if progress:
            progress(15 + round(75 * (index + 1) / MAX_SAMPLES),
                     f"Reviewed source window {index + 1}/{MAX_SAMPLES}")
    status = ("ready" if use_vision and not failures else
              "partial" if use_vision and failures < MAX_SAMPLES else
              "unavailable" if use_vision else "not_requested")
    return recommend_content_mode(transcript, start, duration, entries,
                                  visual_status=status)
