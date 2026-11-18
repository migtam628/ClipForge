"""Source-grounded editorial decisions and asynchronous, read-only review API."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from clipforge import app as module
from clipforge import content_director
from clipforge.recovery import save_json

client = TestClient(module.app)


def _evidence(visuals, speech=""):
    return [{"timestamp": 2 + i * 5, "speech_excerpt": speech,
             "visual_observation": visual} for i, visual in enumerate(visuals)]


def test_source_dialogue_favors_captions_over_invented_story():
    transcript = [{"start": 0, "end": 6, "text": "The speaker explains every detail of "
                   "the equipment and the actual work being performed today"}]
    plan = content_director.recommend_content_mode(
        transcript, 0, 15, _evidence(["A person appears on screen"] * 3),
        visual_status="ready")
    assert plan["suggested_mode"] == "captions_only"
    assert plan["speech_words"] >= 12
    assert plan["review_required"]
    assert plan["analysis_basis"] == "source_speech_and_sampled_ai_frames"


def test_limited_evidence_abstains_and_no_automatic_voiceover():
    plan = content_director.recommend_content_mode(
        [], 0, 15, _evidence(["A road", "A beach", "A sunset"]),
        visual_status="ready")
    assert plan["suggested_mode"] == "no_extra_text"
    assert plan["optional_story_script"] is True
    assert plan["review_required"]
    assert "No narration" in plan["warning"]
    missing = content_director.recommend_content_mode(
        [], 0, 15, _evidence(["", "", ""]), visual_status="unavailable")
    assert missing["suggested_mode"] == "no_extra_text"
    assert missing["confidence"] == "limited"
    assert not missing["optional_story_script"]


def test_context_requires_short_speech_correlated_with_multiple_frames():
    speech = [{"start": 1, "end": 2, "text": "white house exterior"}]
    plan = content_director.recommend_content_mode(
        speech, 0, 15, _evidence(["A white house exterior", "A person inside", ""],
                                "white house exterior"), visual_status="partial")
    assert plan["suggested_mode"] == "context_labels"
    assert plan["context_corroborated_windows"] == 1
    insufficient = content_director.recommend_content_mode(
        speech, 0, 15, _evidence(["A white house exterior", "", ""],
                                "white house exterior"), visual_status="partial")
    assert insufficient["suggested_mode"] == "captions_only"


def test_source_coverage_merges_overlaps_and_skips_invalid_values():
    words, coverage = content_director._source_speech([
        {"start": 0, "end": 5, "text": "First section"},
        {"start": 3, "end": 8, "text": "Second section"},
        {"start": float("nan"), "end": 8, "text": "Ignored"},
        {"start": -5, "end": -1, "text": "Outside"},
        {"start": "bad", "end": 10, "text": "Invalid"},
    ], 0, 10)
    assert words == 4
    assert coverage == 0.8


def test_visual_analysis_is_bounded_and_degrades_to_speech(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    frames = []

    def inspect(_source, scene, _checkpoint):
        frames.append(scene)
        if len(frames) == 2:
            raise RuntimeError("Vision model busy")
        return {"observation": "White house exterior"}

    monkeypatch.setattr(content_director, "_visual_draft", inspect)
    states = []
    result = content_director.analyze_content_range(
        source, [{"start": 10, "end": 11, "text": "white house exterior"}],
        10, 15, tmp_path / "plan.json", use_vision=True,
        progress=lambda percent, stage: states.append((percent, stage)),
    )
    assert len(frames) == 3
    assert [round(f["start"], 2) for f in frames] == [10, 15, 20]
    assert result["sampled_frames"] == 2
    assert result["visual_status"] == "partial"
    assert states[-1][0] == 90
    assert result["review_required"]


def test_content_review_api_returns_pollable_recommendation(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "content review")
    (folder / "source.mp4").write_bytes(b"source")
    module._patch(folder, status="completed", source_file="source.mp4",
                  duration=60, clips=[{"number": 1, "start": 5, "duration": 15}])
    monkeypatch.setattr(module, "vision_status", lambda: {
        "status": "missing", "note": "Local vision model is not installed",
    })
    called = []

    def analyze(_source, _transcript, _start, _duration, _checkpoint, *,
                use_vision, progress):
        called.append(use_vision)
        progress(80, "Speech reviewed")
        return {"suggested_mode": "captions_only", "evidence": [],
                "review_required": True, "visual_status": "not_requested"}

    monkeypatch.setattr(module, "analyze_content_range", analyze)
    url = f"/api/jobs/{job_id}/clips/1/content-review"
    assert client.post(url, json={"start": 55, "duration": 10}).status_code == 422
    assert client.post(url.replace("clips/1", "clips/2"),
                       json={"start": 5, "duration": 15}).status_code == 404
    assert client.post(url, json={"start": 5, "duration": 121}).status_code == 422
    response = client.post(url, json={"start": 5, "duration": 15})
    assert response.status_code == 202
    assert response.json()["status_url"].endswith("/content-review/status")
    state = {}
    for _ in range(100):
        state = client.get(response.json()["status_url"]).json()
        if state.get("state") == "completed":
            break
        time.sleep(0.01)
    assert state["state"] == "completed"
    assert state["progress"] == 100
    assert state["result"]["visual_status"] == "unavailable"
    assert state["result"]["suggested_mode"] == "captions_only"
    assert "token" not in state
    assert called == [False]


def test_conflicting_inflight_review_is_rejected_and_restart_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "content review")
    (folder / "source.mp4").write_bytes(b"source")
    module._patch(folder, status="completed", source_file="source.mp4",
                  duration=60, clips=[{"number": 1, "start": 5, "duration": 15}])
    path = module._content_task_path(folder, 1)
    save_json(path, {"token": "secret", "request": [5, 15, True, "quick"],
                     "state": "running", "progress": 60, "stage": "Analyzing"})
    url = f"/api/jobs/{job_id}/clips/1/content-review"
    assert client.post(url, json={"start": 10, "duration": 15}).status_code == 409
    duplicate = client.post(url, json={"start": 5, "duration": 15})
    assert duplicate.status_code == 202
    assert duplicate.json()["progress"] == 60
    module._recover_interrupted_content_tasks()
    status = client.get(url + "/status").json()
    assert status["state"] == "failed"
    assert "restart" in status["error"]


def test_frame_pair_differences_are_not_claimed_as_verified_motion():
    assert content_director._compare_descriptions("A green tree beside a road",
                                                   "A green tree beside the road") == "similar_descriptions"
    assert content_director._compare_descriptions("A green tree beside the road",
                                                   "A red car near the lake") == "different_descriptions"
    assert content_director._compare_descriptions("", "A red car") == "insufficient_visual_evidence"


def test_temporal_review_uses_three_source_windows_and_six_local_frames(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    events = []

    def inspect(_source, _transcript, _start, _duration, _checkpoint, *, progress):
        progress(90, "Preparing source evidence")
        return {"cuts": [4.0, 10.0], "method": "keyframe_difference",
                "analysis_status": "coarse", "warning": "Approximate cuts",
                "auto_edit": {"segments": [], "removed_seconds": 0,
                              "reason": "no_long_internal_pauses", "review_required": True}}

    def vision(_source, scene, checkpoint):
        stamp = round(scene["start"] + scene["duration"] / 2, 2)
        events.append((stamp, checkpoint.name))
        return {"observation": f"A person at timestamp {stamp} near the table"}

    monkeypatch.setattr(content_director, "inspect_scene_range", inspect)
    monkeypatch.setattr(content_director, "_visual_draft", vision)
    updates = []
    result = content_director.analyze_temporal_range(
        source, [{"start": 0, "end": 12, "text": "The presenter describes a new camera "
                 "setup and explains why the picture is sharper now"}], 0, 15,
        tmp_path / "clip-001-content.json", use_vision=True,
        progress=lambda percent, stage: updates.append((percent, stage)),
    )
    assert result["analysis_depth"] == "sequence"
    assert result["sampled_frames"] == result["max_frames"] == 6
    assert result["suggested_mode"] == "captions_only"
    assert len(result["temporal"]["windows"]) == 3
    assert result["temporal"]["cuts"] == [4, 10]
    assert result["temporal"]["cut_analysis_status"] == "coarse"
    assert [timestamp for timestamp, _ in events] == [2.5, 4.25, 7.5, 9.25, 12.5, 14.25]
    assert events[0][1] == "clip-001-content-frame-0.json"  # reuse quick-frame cache
    assert updates[-1][0] == 90
    assert all(window["review_required"] for window in result["temporal"]["windows"])
    assert "not verified actions" in result["temporal"]["interpretation"]


def test_temporal_review_degrades_without_vision_and_disables_partial_pacing(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(content_director, "inspect_scene_range", lambda *_, **__: {
        "cuts": [], "method": "bounded_frame_difference", "analysis_status": "partial",
        "warning": "Scan timed out", "auto_edit": {"segments": [
            {"start": 0, "duration": 2}, {"start": 5, "duration": 5}],
            "removed_seconds": 3, "reason": "speech_gap", "review_required": True},
    })
    monkeypatch.setattr(content_director, "_visual_draft", lambda *_, **__: (
        1 / 0))  # must not run when visual analysis is disabled
    plan = content_director.analyze_temporal_range(
        source, [], 0, 10, tmp_path / "clip-content.json", use_vision=False,
    )
    assert plan["visual_status"] == "not_requested"
    assert plan["sampled_frames"] == 0
    assert plan["max_frames"] == 4  # short selections use only two windows
    assert plan["temporal"]["cut_analysis_status"] == "partial"
    assert plan["temporal"]["pacing"]["segments"] == []
    assert plan["suggested_mode"] == "no_extra_text"


def test_temporal_review_keeps_speech_evidence_when_cut_decoder_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    def decoder_fails(*_, **__):
        raise RuntimeError("Video could not be decoded")

    monkeypatch.setattr(content_director, "inspect_scene_range", decoder_fails)
    plan = content_director.analyze_temporal_range(
        source, [{"start": 0, "end": 4, "text": "The speaker is explaining an issue "
                 "that needs urgent repair in this particular area"}],
        0, 10, tmp_path / "content.json", use_vision=False,
    )
    assert plan["suggested_mode"] == "captions_only"
    assert plan["temporal"]["cut_analysis_status"] == "partial"
    assert plan["temporal"]["cuts"] == []
    assert plan["temporal"]["pacing"]["segments"] == []


def test_content_review_api_dispatches_sequence_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "temporal review")
    (folder / "source.mp4").write_bytes(b"source")
    module._patch(folder, status="completed", source_file="source.mp4",
                  duration=60, clips=[{"number": 1, "start": 0, "duration": 15}])
    monkeypatch.setattr(module, "vision_status", lambda: {
        "status": "unavailable", "note": "Ollama unreachable",
    })
    calls = []

    def temporal(_source, _transcript, _start, _duration, _checkpoint, *,
                 use_vision, progress):
        calls.append(use_vision)
        progress(60, "Comparing frame pairs")
        return {"suggested_mode": "no_extra_text", "evidence": [],
                "visual_status": "not_requested", "temporal": {
                    "windows": [], "cuts": [], "pacing": {"segments": []}}}

    monkeypatch.setattr(module, "analyze_temporal_range", temporal)
    endpoint = f"/api/jobs/{job_id}/clips/1/content-review"
    assert client.post(endpoint, json={"start": 0, "duration": 15,
                                       "depth": "unknown"}).status_code == 422
    queued = client.post(endpoint, json={"start": 0, "duration": 15,
                                         "depth": "sequence"})
    assert queued.status_code == 202
    state = {}
    for _ in range(100):
        state = client.get(queued.json()["status_url"]).json()
        if state.get("state") == "completed":
            break
        time.sleep(0.01)
    assert state["state"] == "completed"
    assert state["result"]["temporal"]["windows"] == []
    assert state["result"]["vision_note"] == "Ollama unreachable"
    assert calls == [False]


def test_ordered_clip_quick_review_uses_output_order_and_original_source_frames(
        tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    segments = [{"start": 30, "duration": 5}, {"start": 10, "duration": 4},
                {"start": 50, "duration": 6}]
    transcript = [
        {"start": 31, "end": 33, "text": "First topic"},
        {"start": 11, "end": 13, "text": "Second topic"},
        {"start": 52, "end": 55, "text": "Third topic"},
    ]
    sampled = []

    def observe(_source, scene, _checkpoint):
        sampled.append(round(scene["start"] + scene["duration"] / 2, 2))
        return {"observation": "The same speaker appears"}

    monkeypatch.setattr(content_director, "_visual_draft", observe)
    plan = content_director.analyze_content_range(
        source, transcript, 0, 15, tmp_path / "content.json",
        use_vision=True, segments=segments,
    )
    assert sampled == [32.5, 12.5, 53.5]
    assert plan["analysis_context"] == "ordered_sequence"
    assert plan["segment_count"] == 3
    assert [item["timestamp"] for item in plan["evidence"]] == [2.5, 7.5, 12.5]
    assert [item["source_timestamp"] for item in plan["evidence"]] == sampled
    assert [item["speech_excerpt"] for item in plan["evidence"]] == [
        "First topic", "Second topic", "Third topic",
    ]
    assert plan["speech_words"] == 6
    assert plan["transcript_timing"] == "source_segment_approximate"


def test_ordered_clip_scene_sequence_reports_known_joins_without_scanning_or_pacing(
        tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    segments = [{"start": 30, "duration": 5}, {"start": 10, "duration": 4},
                {"start": 50, "duration": 6}]
    monkeypatch.setattr(content_director, "inspect_scene_range", lambda *_, **__: (
        1 / 0))  # edited joins must not be mistaken for visual scene detection
    observed = []

    def vision(_source, scene, _checkpoint):
        observed.append(round(scene["start"], 2))
        return {"observation": "A person near a table"}

    monkeypatch.setattr(content_director, "_visual_draft", vision)
    plan = content_director.analyze_temporal_range(
        source, [], 0, 15, tmp_path / "content.json",
        use_vision=True, segments=segments,
    )
    assert plan["analysis_context"] == "ordered_sequence"
    assert plan["sampled_frames"] == 6
    assert observed == [32.5, 34.25, 12.5, 50.25, 53.5, 55.25]
    assert plan["temporal"]["cuts"] == [5, 9]
    assert plan["temporal"]["cut_method"] == "existing_edit_boundaries"
    assert plan["temporal"]["cut_analysis_status"] == "known_edits"
    assert plan["temporal"]["cut_points"] == [
        {"timestamp": 5, "source_timestamp": 10},
        {"timestamp": 9, "source_timestamp": 50},
    ]
    assert plan["temporal"]["pacing"]["segments"] == []
    assert plan["temporal"]["windows"][1]["after"]["source_timestamp"] == 50.25


def test_content_review_api_accepts_reordered_cuts_and_rejects_invalid_geometry(
        tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "ordered cut review")
    (folder / "source.mp4").write_bytes(b"source")
    module._patch(folder, status="completed", source_file="source.mp4",
                  duration=60, clips=[{"number": 1, "start": 0, "duration": 12}])
    segments = [{"start": 40, "duration": 8}, {"start": 10, "duration": 4}]
    endpoint = f"/api/jobs/{job_id}/clips/1/content-review"
    sample = {"start": 0, "duration": 12, "use_vision": False,
              "segments": segments}
    assert client.post(endpoint, json={**sample, "duration": 14}).status_code == 422
    assert client.post(endpoint, json={**sample, "start": 2}).status_code == 422
    assert client.post(endpoint, json={**sample,
                                      "segments": [{"start": 55, "duration": 8},
                                                   segments[1]]}).status_code == 422
    assert client.post(endpoint, json={**sample, "segments": segments * 7}).status_code == 422
    observed = []

    def review(_source, _transcript, _start, _duration, _checkpoint, *,
               use_vision, progress, segments):
        observed.append(segments)
        progress(80, "Analyzed output order")
        return {"analysis_context": "ordered_sequence", "segment_count": len(segments),
                "suggested_mode": "captions_only", "evidence": [],
                "visual_status": "not_requested"}

    monkeypatch.setattr(module, "analyze_content_range", review)
    queued = client.post(endpoint, json=sample)
    assert queued.status_code == 202
    status = {}
    for _ in range(100):
        status = client.get(queued.json()["status_url"]).json()
        if status.get("state") == "completed":
            break
        time.sleep(0.01)
    assert status["state"] == "completed"
    assert status["result"]["analysis_context"] == "ordered_sequence"
    assert observed == [segments]
