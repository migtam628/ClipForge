"""Regression tests for scene-by-scene, content-grounded script generation."""
import io
import json
import subprocess

import pytest

from clipforge import storyboard
from clipforge.captions import write_storyboard_captions


def evidence():
    return [
        {"start": 0, "end": 8, "text": "The technician opens the control panel."},
        {"start": 9, "end": 17, "text": "The indicator turns green."},
        {"start": 18, "end": 25, "text": "The engineer checks connectivity."},
        {"start": 26, "end": 30, "text": "The final test is ready."},
        {"start": 85, "end": 92, "text": "Unrelated footage, outside the clip."},
    ]


def fake_response(payload, **kwargs):
    import re

    material = json.loads(payload["prompt"].split("Evidence: ", 1)[1])
    assert not re.search("Unrelated footage", payload["prompt"])
    return json.dumps({"beats": [
        {"id": item["id"], "text": "Visible observation or speech at this moment."}
        for item in material
    ]})


def test_windows_span_entire_clip():
    for duration in [10, 15, 30, 60, 120]:
        windows = storyboard.time_windows(duration)
        assert 2 <= len(windows) <= 8
        assert windows[0]["start"] == 0
        assert windows[-1]["end"] == duration
        assert all(left["end"] == right["start"]
                   for left, right in zip(windows, windows[1:]))
    with pytest.raises(ValueError):
        storyboard.time_windows(121)


def test_video_storyboard_samples_each_window_and_saves_timestamps(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    sampled = []
    monkeypatch.setattr(storyboard, "_visual_evidence",
                        lambda src, second: sampled.append(second) or "Green control light")
    monkeypatch.setattr(storyboard, "_ollama", fake_response)
    checkpoint = tmp_path / "scene.json"
    result = storyboard.build_storyboard(
        evidence(), 0, 30, "documentary", "en", checkpoint,
        source=source, basis="video",
    )
    assert len(sampled) == len(result["beats"]) == 4
    assert sampled == pytest.approx([3.75, 11.25, 18.75, 26.25])
    assert result["analysis_basis"] == "video_frames_and_speech"
    assert all(entry["basis"] == "frame+speech" for entry in result["beats"])
    assert "Unrelated footage" not in checkpoint.read_text()


def test_speech_only_never_calls_vision(tmp_path, monkeypatch):
    monkeypatch.setattr(storyboard, "_ollama", fake_response)
    monkeypatch.setattr(storyboard, "_visual_evidence",
                        lambda *_: pytest.fail("Vision called in transcript-only mode"))
    result = storyboard.build_storyboard(
        evidence(), 0, 30, "casual", "es", tmp_path / "speech.json",
        basis="transcript",
    )
    assert result["analysis_basis"] == "speech_only"
    assert result["frame_samples"] == 0
    assert result["beats"][-1]["end"] == 30


def test_silent_video_uses_frames(tmp_path, monkeypatch):
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(storyboard, "_ollama", lambda payload, **kwargs:
                        json.dumps({"beats": [
                            {"id": item["id"], "text": "The camera shows a room."}
                            for item in json.loads(payload["prompt"].split("Evidence: ", 1)[1])
                        ]}))
    monkeypatch.setattr(storyboard, "_visual_evidence",
                        lambda *_: "A room with a red door.")
    result = storyboard.build_storyboard(
        [], 0, 12, "documentary", "en", tmp_path / "visual.json",
        source=source, basis="video",
    )
    assert all(beat["basis"] == "frame" for beat in result["beats"])
    with pytest.raises(RuntimeError, match="No source evidence"):
        storyboard.build_storyboard(
            [], 0, 12, "documentary", "en", tmp_path / "speech.json",
            basis="transcript",
        )


def test_non_evidenced_windows_are_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(storyboard, "_ollama", lambda payload, **kwargs:
                        json.dumps({"beats": [
                            {"id": item["id"], "text": "Claimed something happened."}
                            for item in json.loads(payload["prompt"].split("Evidence: ", 1)[1])
                        ]}))
    result = storyboard.build_storyboard(
        [{"start": 0, "end": 2, "text": "A dog is barking."}],
        0, 30, "casual", "en", tmp_path / "result.json",
        basis="transcript",
    )
    assert result["beats"][0]["text"]
    assert all(not item["text"] for item in result["beats"][1:])


def test_storyboard_rejects_malformed_ai_timeline(tmp_path, monkeypatch):
    monkeypatch.setattr(storyboard, "_ollama", lambda *_: '{"beats":[{"id":0,"text":"a"}]}')
    with pytest.raises(RuntimeError, match="could not return valid timed narration"):
        storyboard.build_storyboard(
            evidence(), 0, 30, "casual", "en", tmp_path / "bad.json",
        )


def test_truncated_timed_script_pair_falls_back_to_individual_beats(tmp_path, monkeypatch):
    calls = []

    def respond(payload, **_kwargs):
        batch = json.loads(payload["prompt"].split("Evidence: ", 1)[1])
        calls.append([item["id"] for item in batch])
        assert len(batch) <= 2
        assert payload["format"]["properties"]["beats"]["maxItems"] == len(batch)
        if calls[-1] == [0, 1]:
            return '{"beats":[{"id":0,"text":"unfinished'
        return json.dumps({"beats": [
            {"id": item["id"], "text": "Narrated from this window's evidence."}
            for item in batch
        ]})

    monkeypatch.setattr(storyboard, "_ollama", respond)
    output = storyboard.build_storyboard(
        evidence(), 0, 30, "documentary", "en", tmp_path / "scene.json",
    )
    assert len(output["beats"]) == 4
    assert all(beat["text"] for beat in output["beats"])
    assert calls == [[0, 1], [0], [1], [2, 3]]


def test_timed_script_drafts_survive_failure_without_regeneration(tmp_path, monkeypatch):
    checkpoint = tmp_path / "script.json"
    first_calls = []

    def fail_second_batch(payload, **_kwargs):
        batch = json.loads(payload["prompt"].split("Evidence: ", 1)[1])
        ids = [item["id"] for item in batch]
        first_calls.append(ids)
        if ids[0] >= 2:
            return '{"beats":[{"id":2,"text":"incomplete'
        return json.dumps({"beats": [
            {"id": item["id"], "text": "First saved narration."} for item in batch
        ]})

    monkeypatch.setattr(storyboard, "_ollama", fail_second_batch)
    with pytest.raises(RuntimeError, match="could not return valid timed narration"):
        storyboard.build_storyboard(evidence(), 0, 30, "casual", "en", checkpoint)
    saved = json.loads((tmp_path / "script-draft.json").read_text())
    assert sorted(saved["texts"]) == ["0", "1"]
    assert first_calls == [[0, 1], [2, 3], [2]]

    next_calls = []

    def finish_remaining(payload, **_kwargs):
        batch = json.loads(payload["prompt"].split("Evidence: ", 1)[1])
        next_calls.append([item["id"] for item in batch])
        return json.dumps({"beats": [
            {"id": item["id"], "text": "Recovered later narration."} for item in batch
        ]})

    monkeypatch.setattr(storyboard, "_ollama", finish_remaining)
    output = storyboard.build_storyboard(evidence(), 0, 30, "casual", "en", checkpoint)
    assert next_calls == [[2, 3]]
    assert output["beats"][0]["text"] == "First saved narration."
    assert output["beats"][-1]["text"] == "Recovered later narration."


def test_existing_v3_frame_observations_are_reused_after_script_update(
        tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original video")
    checkpoint = tmp_path / "clip-001-story.json"
    windows = storyboard.time_windows(12)
    stat = source.stat()
    import hashlib

    signature = hashlib.sha256(json.dumps([
        3, str(source.resolve()), stat.st_size, stat.st_mtime_ns,
        "moondream", 0, 12, windows,
    ], sort_keys=True).encode()).hexdigest()
    (tmp_path / "clip-001-story-frames.json").write_text(json.dumps({
        "signature": signature,
        "observations": {"0": "A storefront.", "1": "A pedestrian."},
    }))
    monkeypatch.setenv("CLIPFORGE_VISION_MODEL", "moondream")
    monkeypatch.setattr(storyboard, "_visual_evidence",
                        lambda *_: pytest.fail("Old frame cache was ignored"))
    monkeypatch.setattr(storyboard, "_ollama", fake_response)
    output = storyboard.build_storyboard([], 0, 12, "casual", "en", checkpoint,
                                         source=source, basis="video")
    assert len(output["beats"]) == 2


def test_timed_voice_assembly_and_captions_reach_last_scene(tmp_path, monkeypatch):
    from clipforge import narration

    script = {"beats": [
        {"start": 0, "end": 1.3, "text": "Beginning"},
        {"start": 1.3, "end": 2.5, "text": ""},
        {"start": 2.5, "end": 4.0, "text": "Closing"},
    ]}
    calls = []

    def fake_synthesize(words, file):
        if file.is_file():
            return file
        calls.append(words)
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.7",
            "-c:a", "libmp3lame", str(file),
        ], check=True, capture_output=True)
        return file

    monkeypatch.setattr(narration, "synthesize", fake_synthesize)
    output, beats = storyboard.synthesize_timed_voice(script, tmp_path, "test", 4.0)
    assert output.is_file()
    assert calls == ["Beginning", "Closing"]
    assert 3.9 < narration.audio_duration(output) < 4.2
    captions = tmp_path / "captions.ass"
    assert write_storyboard_captions(beats, 4.0, "bold", captions)
    assert "CLOSING" in captions.read_text()
    assert "0:00:02." in captions.read_text()
    # Cached final audio should not bill again for the same job.
    output2, _ = storyboard.synthesize_timed_voice(script, tmp_path, "test", 4.0)
    assert output2 == output
    assert len(calls) == 2  # cached segment outputs avoid repeat TTS charges


def test_settings_exposes_vision_without_secret(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from clipforge import app as module

    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    resp = TestClient(module.app).get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["vision"]["model"]
    assert "api_key" not in json.dumps(resp.json()["vision"]).lower()
    page = TestClient(module.app).get("/studio").text
    assert 'id="narration-basis"' in page


def test_vision_retries_blank_response_with_different_frame(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPFORGE_VISION_MODEL", "moondream")
    sampled = []
    prompts = []

    def sample_frame(src, timestamp):
        sampled.append((src, timestamp))
        return b"valid-JPEG-placeholder"

    responses = iter(["  ", "A person standing beside a red car."])
    def model_response(payload, **kwargs):
        assert kwargs["vision"]
        assert payload["stream"] is False
        assert payload["keep_alive"] == "5m"
        assert payload["options"]["num_predict"] >= 160
        prompts.append(payload["prompt"])
        return next(responses)

    monkeypatch.setattr(storyboard, "_frame", sample_frame)
    monkeypatch.setattr(storyboard, "_ollama", model_response)
    source = tmp_path / "source.mp4"
    assert storyboard._visual_evidence(source, 12.0) == (
        "A person standing beside a red car."
    )
    assert sampled == [(source, 12.0), (source, 11.25)]
    assert prompts == [
        "Describe this image in one short sentence.",
        "What can you see in this image?",
    ]


def test_vision_refuses_to_fabricate_after_two_blank_frames(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPFORGE_VISION_MODEL", "moondream")
    frames = []
    monkeypatch.setattr(storyboard, "_frame",
                        lambda _, t: frames.append(t) or b"jpeg")
    monkeypatch.setattr(storyboard, "_ollama", lambda *_args, **_kwargs: "")
    with pytest.raises(RuntimeError, match="not fabricated"):
        storyboard._visual_evidence(tmp_path / "source.mp4", 4)
    assert frames == [4, 3.25]


def test_vision_success_does_not_unnecessarily_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPFORGE_VISION_MODEL", "moondream")
    seen = []
    monkeypatch.setattr(storyboard, "_frame",
                        lambda _src, t: seen.append(t) or b"frame")
    monkeypatch.setattr(storyboard, "_ollama",
                        lambda *_args, **_kwargs: "A street with shops.")
    assert storyboard._visual_evidence(tmp_path / "source.mp4", 10) == (
        "A street with shops."
    )
    assert seen == [10]


def test_blank_ollama_vision_response_logs_safe_metadata(monkeypatch, caplog):
    monkeypatch.setenv("CLIPFORGE_OLLAMA_URL", "http://ollama:11434")
    result = {
        "response": "", "done_reason": "stop", "eval_count": 0,
        "prompt_eval_count": 745, "total_duration": 42_000_000_000,
    }
    monkeypatch.setattr(storyboard, "urlopen", lambda *_args, **_kwargs:
                        io.BytesIO(json.dumps(result).encode()))
    with caplog.at_level("WARNING", logger="uvicorn.error"):
        assert storyboard._ollama({"model": "moondream", "prompt": "private prompt"},
                                  vision=True) == ""
    assert "done_reason=stop" in caplog.text
    assert "output_tokens=0" in caplog.text
    assert "prompt_tokens=745" in caplog.text
    assert "duration_ms=42000" in caplog.text
    assert "private prompt" not in caplog.text


def test_video_frame_observations_survive_failed_resume(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original video")
    checkpoint = tmp_path / "narration-story.json"
    sampled = []

    def interrupted(_src, timestamp):
        sampled.append(timestamp)
        if len(sampled) == 2:
            raise RuntimeError("temporary vision failure")
        return "A small shop on a city street."

    monkeypatch.setattr(storyboard, "_visual_evidence", interrupted)
    with pytest.raises(RuntimeError, match="temporary vision failure"):
        storyboard.build_storyboard([], 0, 12, "documentary", "en", checkpoint,
                                    source=source, basis="video")
    saved = json.loads((tmp_path / "narration-story-frames.json").read_text())
    assert saved["observations"] == {"0": "A small shop on a city street."}

    monkeypatch.setattr(storyboard, "_visual_evidence",
                        lambda _src, timestamp: sampled.append(timestamp) or
                        "A pedestrian beside the shop.")
    monkeypatch.setattr(storyboard, "_ollama", fake_response)
    result = storyboard.build_storyboard([], 0, 12, "documentary", "en", checkpoint,
                                         source=source, basis="video")
    assert len(result["beats"]) == 2
    assert sampled == pytest.approx([3.0, 9.0, 9.0])


def test_video_frame_cache_invalidates_on_source_change(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"v1")
    checkpoint = tmp_path / "story.json"
    sampled = []
    monkeypatch.setattr(storyboard, "_visual_evidence",
                        lambda _src, timestamp: sampled.append(timestamp) or "A street.")
    monkeypatch.setattr(storyboard, "_ollama", fake_response)
    for _ in range(2):
        storyboard.build_storyboard([], 0, 12, "casual", "en", checkpoint,
                                    source=source, basis="video")
    assert sampled == pytest.approx([3.0, 9.0])
    source.write_bytes(b"a different source video")
    storyboard.build_storyboard([], 0, 12, "casual", "en", checkpoint,
                                source=source, basis="video")
    assert sampled == pytest.approx([3.0, 9.0, 3.0, 9.0])
