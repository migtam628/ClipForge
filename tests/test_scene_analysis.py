"""Bounded scene detection and editorial evidence for selected clip ranges."""
from __future__ import annotations

import subprocess

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
    monkeypatch.setattr(scene_analysis, "detect_scene_cuts",
                        lambda *_args: (calls.append(True) or [3.0, 7.0]))
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
    assert len(first["scenes"]) == 3
    assert "camera angles" in first["scenes"][0]["speech_excerpt"]
    assert "different spoken" in second["scenes"][2]["speech_excerpt"]
    assert "camera angles" not in second["scenes"][2]["speech_excerpt"]


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

    monkeypatch.setattr(module, "inspect_scene_range", lambda *_args: {
        "cuts": [10.0], "scenes": [{"start": 5.0, "duration": 5.0,
                                  "speech_excerpt": "Source text"}],
        "method": "frame_difference", "cached": False,
    })
    response = client.post(endpoint, json={"start": 5, "duration": 20})
    assert response.status_code == 200
    assert response.json()["cuts"] == [10]


def test_scene_range_rejects_invalid_inputs(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"example")
    with pytest.raises(ValueError):
        scene_analysis.detect_scene_cuts(source, 0, 125)
    with pytest.raises(ValueError):
        scene_analysis.detect_scene_cuts(source, 0, 10, threshold=float("nan"))


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
