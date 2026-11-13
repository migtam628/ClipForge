"""AI Engine 2.0 quality gates: speech-safe trims and honest export audits."""
from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from clipforge import quality
from clipforge.app import app


def test_refinement_extends_only_existing_speech_intersections():
    candidates = [{"start": 30.0, "duration": 30.0, "score": 9.5}]
    speech = [
        {"start": 28.5, "end": 35, "text": "Setup begins before clip"},
        {"start": 55, "end": 62, "text": "Sentence ends after clip"},
    ]
    actual = quality.refine_speech_boundaries(candidates, speech, 90)
    assert len(actual) == 1
    assert actual[0]["start"] == 28.5
    assert actual[0]["duration"] == 33.5
    assert actual[0]["speech_boundary_refined"]
    assert actual[0]["boundary_review"] == []
    assert candidates[0] == {"start": 30.0, "duration": 30.0, "score": 9.5}


def test_long_utterances_are_flagged_not_silently_cut():
    candidate = [{"start": 30, "duration": 30}]
    result = quality.refine_speech_boundaries(candidate, [
        {"start": 15, "end": 37},
        {"start": 54, "end": 78},
    ], 100)
    assert result[0]["start"] == 30
    assert result[0]["duration"] == 30
    assert result[0]["boundary_review"] == [
        "possible_mid_sentence_end", "possible_mid_sentence_start"
    ]


def test_adjacent_clips_never_overlap_after_refinement():
    candidates = [
        {"start": 0, "duration": 30},
        {"start": 30, "duration": 30},
    ]
    speech = [{"start": 29, "end": 32}]
    result = quality.refine_speech_boundaries(candidates, speech, 70)
    assert result[0]["duration"] == 30
    assert "possible_mid_sentence_end" in result[0]["boundary_review"]
    assert result[1]["start"] >= result[0]["start"] + result[0]["duration"]
    assert "possible_mid_sentence_start" in result[1]["boundary_review"]


def test_refinement_respects_silence_and_invalid_segments():
    candidates = [{"start": 30, "duration": 30}]
    transcript = [{"start": "broken", "end": 60},
                  {"start": float("nan"), "end": 31},
                  {"start": 31, "end": 39},
                  {"start": 100, "end": 120}]
    result = quality.refine_speech_boundaries(candidates, transcript, 65)
    assert result[0]["start"] == 30
    assert result[0]["duration"] == 30
    assert not result[0]["speech_boundary_refined"]
    with pytest.raises(ValueError):
        quality.refine_speech_boundaries(candidates, [], -2)


def test_editorial_checks_do_not_ever_claim_publish_ready(monkeypatch, tmp_path):
    path = tmp_path / "render.mp4"
    path.write_bytes(b"a" * 1024)
    monkeypatch.setattr(quality, "_media_inspection", lambda _: {
        "duration": 30.1, "width": 720, "height": 1280, "has_audio": True,
    })
    result = quality.inspect_rendered_clip(
        path, expected_duration=30, aspect_ratio="9:16", source_has_audio=True,
        narration="narrated", script="The event happens.",
        beats=[{"start": 0, "end": 15, "text": "A repeated long speech beat"},
               {"start": 15, "end": 30, "text": "A repeated long speech beat"}],
        captions_requested=True, captions_added=True,
        candidate={"score": 8, "creator_niche": "sports"},
    )
    assert result["technical_pass"]
    assert result["status"] == "review_required"
    assert result["publish_ready_verified"] is False
    assert "repeated_narration_beats" in result["review_flags"]
    assert "factual_claims_need_source_verification" in result["review_flags"]
    assert not result["technical_issues"]


def test_broken_render_is_flagged_not_labeled_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(quality, "_media_inspection", lambda _: {
        "duration": 26, "width": 1280, "height": 720, "has_audio": False,
    })
    result = quality.inspect_rendered_clip(
        tmp_path / "anything.mp4", expected_duration=30, aspect_ratio="9:16",
        source_has_audio=True, narration="none", script=None, beats=[],
        captions_requested=True, captions_added=False, candidate={"score": 4},
    )
    assert not result["technical_pass"]
    assert result["status"] == "technical_issue"
    assert "expected_audio_missing" in result["technical_issues"]
    assert "render_resolution_mismatch" in result["technical_issues"]
    assert "render_duration_mismatch" in result["technical_issues"]
    assert "requested_captions_unavailable" in result["review_flags"]
    assert "weak_highlight_score" in result["review_flags"]


def test_genuine_export_with_ffmpeg_is_technically_checked(tmp_path):
    source = tmp_path / "source.mp4"
    clip = tmp_path / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
        "-i", "testsrc2=size=144x80:rate=12:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", str(source),
    ], check=True, capture_output=True)
    from clipforge.engine import render_clip

    render_clip(source, clip, 0, 1.5, "9:16")
    report = quality.inspect_rendered_clip(
        clip, expected_duration=1.5, aspect_ratio="9:16",
        source_has_audio=True, narration="none", script=None, beats=[],
        captions_requested=False, captions_added=False, candidate={"score": 8},
    )
    assert report["technical_pass"]
    assert report["metrics"]["width"] == 720
    assert report["metrics"]["height"] == 1280
    assert report["metrics"]["audio_present"] is True


def test_quality_badges_and_inspector_present_on_library():
    client = TestClient(app)
    javascript = client.get("/assets/app.js").text
    css = client.get("/assets/themes.css").text
    assert "quality_report" in javascript
    assert "View export quality report" in javascript
    assert "Editorial review needed" in javascript
    assert ".quality-pill" in css
