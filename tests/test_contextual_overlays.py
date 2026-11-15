"""Source-grounded on-screen context is separate from subtitles and voiceovers."""
from __future__ import annotations

import json
import subprocess
import time

from fastapi.testclient import TestClient

from clipforge import app as module
from clipforge import storyboard
from clipforge.captions import write_context_overlays
from clipforge.engine import render_clip


def test_on_screen_context_writes_timed_sanitized_labels(tmp_path):
    ass = tmp_path / "context.ass"
    assert write_context_overlays([
        {"start": 0, "end": 5, "text": r"One {untrusted}\Nscene label"},
        {"start": 5, "end": 10, "text": "The discussion moves to phone stands"},
        {"start": 10, "end": 15, "text": ""},
    ], 15, ass, has_hook=True)
    output = ass.read_text(encoding="utf-8")
    assert "0:00:03.42" in output  # opening hook owns the first 3.3 seconds
    assert "0:00:05.12" in output
    assert "phone stands" in output.replace(r"\N", " ")
    assert "{untrusted}" not in output
    assert r"\Nscene" not in output
    assert output.count("Dialogue: ") == 2
    assert "Alignment" in output and "Context" in output
    assert not write_context_overlays([{"start": 0, "end": 5, "text": ""}],
                                      5, tmp_path / "empty.ass")


def test_context_prompt_does_not_request_spoken_narration(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_AI_MODEL", "llama3.2:3b")
    prompts = []

    def fake_ollama(payload, **_kwargs):
        prompts.append(payload["prompt"])
        windows = json.loads(payload["prompt"].split("Evidence: ", 1)[1])
        return json.dumps({"beats": [
            {"id": window["id"], "text": "A discussion about camera framing"}
            for window in windows
        ]})

    monkeypatch.setattr(storyboard, "_ollama", fake_ollama)
    result = storyboard.build_storyboard([
        {"start": 0, "end": 12, "text": "They discuss camera framing", "words": []},
    ], 0, 12, "casual", "en", tmp_path / "context.json",
        basis="transcript", purpose="overlay")
    assert result["beats"]
    assert all(len(beat["text"].split()) <= 10 for beat in result["beats"])
    assert "ON-SCREEN contextual label" in prompts[0]
    assert "NOT spoken narration" in prompts[0]
    assert "not repeat dialogue verbatim" in prompts[0]
    assert "No generic introductions" in prompts[0]


def test_contextual_job_preserves_original_audio_and_renders_overlay(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    opts = module.Options(
        narration="context", narration_basis="speech", caption_style="none",
        selection_mode="energy", max_clips=1,
    )
    _, folder = module._create("upload", opts, "source.mp4")
    (folder / "source.mp4").write_bytes(b"source")
    monkeypatch.setattr(module, "probe", lambda _path: {
        "has_audio": True, "duration": 20.0,
    })
    monkeypatch.setattr(module, "audio_energy", lambda *_: [0.0] * 20)
    monkeypatch.setattr(module, "transcribe", lambda *_: [
        {"start": 0, "end": 20, "text": "A conversation about the phone mount", "words": []},
    ])
    monkeypatch.setattr(module, "select_highlights", lambda *_args, **_kwargs: [
        {"start": 0.0, "duration": 20.0, "score": 1.0},
    ])
    purposes = []

    def fake_storyboard(*_args, **kwargs):
        purposes.append(kwargs["purpose"])
        return {
            "script": "Discussing the phone mount",
            "beats": [{"start": 0, "end": 10, "text": "Discussing the phone mount"}],
            "analysis_basis": "speech_only", "frame_samples": 0,
        }

    monkeypatch.setattr(module, "build_storyboard", fake_storyboard)
    monkeypatch.setattr(module, "synthesize_timed_voice",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("No TTS")))
    rendered = []

    def fake_render(_src, destination, *_args, **kwargs):
        rendered.append(kwargs)
        destination.write_bytes(b"encoded" * 400)

    monkeypatch.setattr(module, "render_clip", fake_render)
    monkeypatch.setattr(module, "render_thumbnail",
                        lambda _src, destination: destination.write_bytes(b"jpg"))
    module._process(folder)
    job = module._read(folder)
    assert job["status"] == "completed", job["error"]
    assert purposes == ["overlay"]
    assert rendered[0]["context"].is_file()
    assert rendered[0]["voiceover"] is None
    assert rendered[0]["has_source_audio"] is True
    assert job["clips"][0]["context_added"] is True
    assert job["clips"][0]["voiceover_added"] is False


def test_ffmpeg_context_layer_keeps_audio_without_voiceover(tmp_path):
    original = tmp_path / "original.mp4"
    output = tmp_path / "context.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", str(original),
    ], check=True, capture_output=True)
    context = tmp_path / "context.ass"
    assert write_context_overlays([
        {"start": 0, "end": 2, "text": "A blue background"},
    ], 2, context)
    render_clip(original, output, 0, 2, "9:16", context=context)
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output),
    ], check=True, capture_output=True, text=True)
    streams = json.loads(result.stdout)["streams"]
    assert any(stream["codec_type"] == "video" for stream in streams)
    assert any(stream["codec_type"] == "audio" for stream in streams)


def test_clip_revision_can_keep_reviewed_context_without_tts(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(narration="context"),
                                    "sample.mp4")
    (folder / "source.mp4").write_bytes(b"source")
    (folder / "clips").mkdir(exist_ok=True)
    (folder / "clips" / "clip-001.mp4").write_bytes(b"old clip")
    beats = [
        {"start": 0, "end": 6, "text": "A box is opened"},
        {"start": 6, "end": 12, "text": "Contents are examined"},
    ]
    module._patch(folder, status="completed", duration=30, source_file="source.mp4",
                  clips=[{
                      "number": 1, "filename": "clip-001.mp4", "start": 2.0,
                      "duration": 12.0, "title": "Unboxing", "context_added": True,
                      "storyboard": beats, "captions_added": False,
                  }])
    monkeypatch.setattr(module, "probe", lambda _source: {
        "has_audio": True, "duration": 30.0,
    })
    output_layers = []

    def fake_render(_source, path, *_args, **kwargs):
        output_layers.append(kwargs)
        path.write_bytes(b"new clip" * 100)

    monkeypatch.setattr(module, "render_clip", fake_render)
    monkeypatch.setattr(module, "render_thumbnail",
                        lambda _source, path: path.write_bytes(b"thumbnail"))
    reply = TestClient(module.app).post(
        f"/api/jobs/{job_id}/clips/1/edit", json={
            "start": 2.0, "duration": 12.0, "caption_style": "none",
            "hook_style": "none", "context_overlay": True,
            "manual_beats": beats,
        })
    assert reply.status_code == 202, reply.text
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        clip = module._read(folder)["clips"][0]
        if clip.get("revision_state") in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert clip["revision_state"] == "completed", clip.get("revision_error")
    assert output_layers[0]["context"].is_file()
    assert output_layers[0]["voiceover"] is None
    assert clip["context_added"] is True
    assert clip["narration"] == "context"
    assert clip["voiceover_added"] is False


def test_context_mode_and_story_voiceover_are_exclusive():
    from pydantic import ValidationError

    try:
        module.ClipEditRequest(start=0, duration=10, context_overlay=True,
                               voiceover=True, voiceover_approved=True)
    except ValidationError as error:
        assert "Choose either contextual text" in str(error)
    else:
        raise AssertionError("Conflicting sound and text modes were accepted")
