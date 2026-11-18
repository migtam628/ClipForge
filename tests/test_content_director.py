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
    save_json(path, {"token": "secret", "request": [5, 15, True],
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
