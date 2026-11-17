"""Bounded scene detection and editorial evidence for selected clip ranges."""
from __future__ import annotations

import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from clipforge import app as module
from clipforge import scene_analysis

client = TestClient(module.app)


def test_visual_scene_cuts_ignore_edges_and_duplicate_events(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"example")
    observed = []

    def fake_run(command, **kwargs):
        observed.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "\n".join([
            "[Parsed_showinfo_3] n:0 pts:0 pts_time:0.25",
            "[Parsed_showinfo_3] n:1 pts:0 pts_time:2.0",
            "[Parsed_showinfo_3] n:2 pts:0 pts_time:2.4",
            "[Parsed_showinfo_3] n:3 pts:0 pts_time:8.75",
            "[Parsed_showinfo_3] n:4 pts:0 pts_time:9.8",
        ]))

    monkeypatch.setattr(scene_analysis.subprocess, "run", fake_run)
    assert scene_analysis.detect_scene_cuts(source, 30, 10) == [32.0, 38.75]
    command, options = observed[0]
    assert "-t" in command and command[command.index("-t") + 1] == "10.000"
    assert "scale=160" in command[command.index("-vf") + 1]
    assert options["timeout"] <= 180


def test_scene_cut_cache_reuses_visual_detection_and_rebuilds_speech(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"example")
    calls = []
    monkeypatch.setattr(scene_analysis, "_detect_scene_details",
                        lambda *_args, **_kwargs: (calls.append(True) or {
                            "cuts": [3.0, 7.0], "method": "keyframe_difference",
                            "analysis_status": "coarse", "warning": "Review approximate cuts",
                        }))
    checkpoint = tmp_path / "cuts.json"
    first = scene_analysis.inspect_scene_range(
        source, [{"start": 0, "end": 4, "text": "Talking about camera angles"}],
        0, 10, checkpoint,
    )
    second = scene_analysis.inspect_scene_range(
        source, [{"start": 7, "end": 9, "text": "A different spoken moment"}],
        0, 10, checkpoint,
    )
    assert len(calls) == 1
    assert not first["cached"] and second["cached"]
    assert second["method"] == "keyframe_difference"
    assert second["warning"] == "Review approximate cuts"
    assert len(first["scenes"]) == 3
    assert "camera angles" in first["scenes"][0]["speech_excerpt"]
    assert "different spoken" in second["scenes"][2]["speech_excerpt"]
    assert "camera angles" not in second["scenes"][2]["speech_excerpt"]


def test_pacing_suggests_only_internal_long_pauses_and_keeps_ends():
    transcript = [
        {"start": 4, "end": 7, "text": "First reaction"},
        {"start": 14, "end": 19, "text": "Next reaction"},
        {"start": 21, "end": 27, "text": "Final reaction"},
    ]
    suggestion = scene_analysis.suggest_speech_pacing(transcript, 0, 30)
    assert suggestion["review_required"] is True
    assert suggestion["segments"] == [
        {"start": 0, "duration": 7.5},
        {"start": 13.5, "duration": 16.5},
    ]
    assert suggestion["removed_seconds"] == 6
    assert scene_analysis.suggest_speech_pacing([], 0, 30)["segments"] == []
    assert scene_analysis.suggest_speech_pacing([
        {"start": 1, "end": 3, "text": "One"},
        {"start": 3.5, "end": 6, "text": "Two"},
    ], 0, 10)["segments"] == []


def test_scene_visual_draft_is_explicitly_unverified_and_cached(tmp_path, monkeypatch):
    from clipforge import storyboard

    source = tmp_path / "source.mp4"
    source.write_bytes(b"example")
    monkeypatch.setattr(scene_analysis, "_detect_scene_details", lambda *_, **__: {
        "cuts": [5.0], "method": "sampled_frame_difference",
        "analysis_status": "complete", "warning": None,
    })
    calls = []
    monkeypatch.setattr(storyboard, "_visual_evidence", lambda *_args: (
        calls.append(True) or "A person appears in a room."))
    checkpoint = tmp_path / "scene.json"
    first = scene_analysis.inspect_scene_range(
        source, [], 0, 10, checkpoint, vision_scene=0)
    second = scene_analysis.inspect_scene_range(
        source, [], 0, 10, checkpoint, vision_scene=0)
    assert len(calls) == 1
    assert first["visual_draft"]["review_required"]
    assert first["visual_draft"]["basis"] == "one_ai_described_video_frame"
    assert not first["visual_draft"]["cached"]
    assert second["visual_draft"]["cached"]
    with pytest.raises(ValueError, match="Scene index"):
        scene_analysis.inspect_scene_range(source, [], 0, 10, checkpoint, vision_scene=9)


def test_scene_cuts_api_validates_job_clip_and_bounded_range(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "test")
    (folder / "source.mp4").write_bytes(b"sample")
    module._patch(folder, status="completed", source_file="source.mp4",
                  duration=60, clips=[{"number": 1, "start": 5, "duration": 20}])
    endpoint = f"/api/jobs/{job_id}/clips/1/scene-cuts"
    assert client.post(endpoint, json={"start": 55, "duration": 10}).status_code == 422
    assert client.post(endpoint.replace("clips/1", "clips/99"),
                       json={"start": 5, "duration": 20}).status_code == 404
    assert client.post(endpoint, json={"start": 5, "duration": 200}).status_code == 422

    monkeypatch.setattr(module, "inspect_scene_range", lambda *_args, **_kwargs: {
        "cuts": [10.0], "scenes": [{"start": 5.0, "duration": 5.0,
                                  "speech_excerpt": "Source text"}],
        "method": "frame_difference", "cached": False,
    })
    response = client.post(endpoint, json={"start": 5, "duration": 20})
    assert response.status_code == 200
    assert response.json()["cuts"] == [10]
    assert client.post(endpoint, json={"start": 5, "duration": 20,
                                       "vision_scene": -1}).status_code == 422


def test_scene_range_rejects_invalid_inputs(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"example")
    with pytest.raises(ValueError):
        scene_analysis.detect_scene_cuts(source, 0, 125)
    with pytest.raises(ValueError):
        scene_analysis.detect_scene_cuts(source, 0, 10, threshold=float("nan"))


def test_keyframe_pass_stops_before_expensive_detailed_pass(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"example")
    calls = []

    def fake_scan(_source, _start, _duration, _threshold, *, keyframes_only, timeout):
        calls.append((keyframes_only, timeout))
        return [3.0] if keyframes_only else [4.0]

    monkeypatch.setattr(scene_analysis, "_scan_frames", fake_scan)
    report = scene_analysis._detect_scene_details(source, 0, 10)
    assert report["cuts"] == [3.0]
    assert report["analysis_status"] == "coarse"
    assert calls == [(True, 10)]


def test_detailed_fallback_after_empty_keyframes(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"example")
    calls = []

    def fake_scan(_source, _start, _duration, _threshold, *, keyframes_only, timeout):
        calls.append(keyframes_only)
        return [] if keyframes_only else [5.0]

    monkeypatch.setattr(scene_analysis, "_scan_frames", fake_scan)
    report = scene_analysis._detect_scene_details(source, 0, 10)
    assert report["cuts"] == [5.0]
    assert report["analysis_status"] == "complete"
    assert calls == [True, False]


def test_two_timeout_passes_return_incomplete_not_false_no_cuts(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"example")
    calls = []

    def time_out(_source, _start, _duration, _threshold, *, keyframes_only, timeout):
        calls.append((keyframes_only, timeout))
        raise TimeoutError("slow decoder")

    monkeypatch.setattr(scene_analysis, "_scan_frames", time_out)
    checkpoint = tmp_path / "cuts.json"
    result = scene_analysis.inspect_scene_range(source, [], 177.66, 10, checkpoint)
    assert result["analysis_status"] == "partial"
    assert "no reliable cut list" in result["warning"]
    assert not checkpoint.exists()
    assert calls == [(True, 10), (False, 12)]


def test_async_scene_api_reports_progress_and_result(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "async test")
    (folder / "source.mp4").write_bytes(b"sample")
    module._patch(folder, status="completed", source_file="source.mp4",
                  duration=60, clips=[{"number": 1, "start": 5, "duration": 20}])

    def fake_inspect(_source, _transcript, _start, _duration, _checkpoint, *, progress=None):
        if progress:
            progress(20, "Scanning video keyframes")
            progress(90, "Preparing scene evidence")
        return {"cuts": [10.0], "scenes": [{"start": 5.0, "duration": 5.0}],
                "analysis_status": "coarse", "warning": "Review approximate cuts"}

    monkeypatch.setattr(module, "inspect_scene_range", fake_inspect)
    endpoint = f"/api/jobs/{job_id}/clips/1/scene-cuts"
    created = client.post(endpoint, json={"start": 5, "duration": 10, "async": True})
    assert created.status_code == 202
    status = None
    for _ in range(100):
        status = client.get(created.json()["status_url"])
        if status.json().get("state") == "completed":
            break
        time.sleep(0.01)
    assert status is not None and status.status_code == 200
    assert status.json()["state"] == "completed"
    assert status.json()["progress"] == 100
    assert status.json()["result"]["cuts"] == [10.0]
    assert "token" not in status.json()
    assert client.post(endpoint, json={"start": 5, "duration": 10, "async": True,
                                       "vision_scene": 0}).status_code == 422


def test_sync_scene_api_returns_review_warning_when_both_passes_time_out(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "slow media")
    (folder / "source.mp4").write_bytes(b"sample")
    module._patch(folder, status="completed", source_file="source.mp4",
                  duration=210, clips=[{"number": 1, "start": 177.66, "duration": 30}])

    def timeout_scan(*_args, **_kwargs):
        raise TimeoutError("decoder too slow")

    monkeypatch.setattr(scene_analysis, "_scan_frames", timeout_scan)
    result = client.post(f"/api/jobs/{job_id}/clips/1/scene-cuts",
                         json={"start": 177.66, "duration": 10})
    assert result.status_code == 200
    assert result.json()["analysis_status"] == "partial"
    assert "Try a shorter selection" in result.json()["warning"]
    assert result.json()["cuts"] == []


def test_restart_marks_interrupted_scene_task_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "restart test")
    checkpoint = module._scene_task_path(folder, 1)
    module.save_json(checkpoint, {"state": "running", "progress": 20, "token": "test"})
    module._recover_interrupted_scene_tasks()
    status = module.load_json(checkpoint)
    assert status["state"] == "failed"
    assert "restart" in status["error"].lower()


def test_ffmpeg_detects_a_real_shot_transition(tmp_path):
    source = tmp_path / "two-scenes.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
        "-f", "lavfi", "-i", "color=c=red:s=160x90:r=10:d=2",
        "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10:d=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "mpeg4", "-q:v", "4", str(source),
    ], capture_output=True, check=True, timeout=30)
    cuts = scene_analysis.detect_scene_cuts(source, 0, 4, threshold=0.15)
    assert any(1.5 <= cut <= 2.5 for cut in cuts), cuts
    shifted = scene_analysis.detect_scene_cuts(source, 0.75, 3, threshold=0.15)
    assert any(1.5 <= cut <= 2.5 for cut in shifted), shifted


def test_scene_scan_seeks_into_long_source_without_scanning_entire_video(tmp_path):
    source = tmp_path / "long-source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
        "-f", "lavfi", "-i", "color=c=red:s=160x90:r=2:d=179",
        "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=2:d=7",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "mpeg4", "-q:v", "4", str(source),
    ], capture_output=True, check=True, timeout=30)
    cuts = scene_analysis.detect_scene_cuts(source, 177.66, 8, threshold=0.15)
    assert any(178.5 <= cut <= 179.5 for cut in cuts), cuts
