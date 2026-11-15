"""Non-destructive Clip Library editor and safe-zone hook regression tests."""
from __future__ import annotations

import subprocess
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from clipforge import app as module
from clipforge import engine
from clipforge.captions import write_hook
from clipforge.engine import render_clip

client = TestClient(module.app)


def _completed_job(tmp_path):
    job_id, folder = module._create(
        "upload", module.Options(aspect_ratio="9:16", caption_style="none"),
        "original video",
    )
    (folder / "source.mp4").write_bytes(b"original-video")
    output = folder / "clips"
    output.mkdir(exist_ok=True)
    (output / "clip-001.mp4").write_bytes(b"previous-media")
    (output / "clip-001.jpg").write_bytes(b"old-thumb")
    module._patch(
        folder, status="completed", duration=60,
        source_file="source.mp4",
        clips=[{
            "filename": "clip-001.mp4", "thumbnail": "clip-001.jpg", "number": 1,
            "start": 5.0, "duration": 20.0,
            "title": "Original", "effect": "none", "framing": "crop",
            "captions_added": False, "hook_style": "none",
        }],
    )
    return job_id, folder


def test_hook_writer_escapes_markup_and_uses_short_duration(tmp_path):
    path = tmp_path / "hook.ass"
    assert write_hook(r"THIS {bad}\Nhook from AI", 30, path)
    text = path.read_text()
    assert "Hook" in text and "Dialogue:" in text
    assert r"\NH" not in text
    assert "{bad}" not in text
    assert "0:00:03.20" in text
    assert not write_hook("", 30, path)
    assert not write_hook("Title", 0.5, path)


def test_editing_job_keeps_prior_clip_and_regenerates_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = _completed_job(tmp_path)
    (folder / "clipforge-clips.zip").write_bytes(b"old-cache")
    observed = []

    def fake_render(source, dest, start, duration, ratio, **kw):
        observed.append((start, duration, ratio, kw))
        dest.write_bytes(b"new-mp4-bytes" * 100)

    monkeypatch.setattr(module, "probe", lambda *_: {"duration": 60, "has_audio": False})
    monkeypatch.setattr(module, "render_clip", fake_render)
    monkeypatch.setattr(module, "render_thumbnail", lambda src, dest: dest.write_bytes(b"jpg"))
    response = client.post(f"/api/jobs/{job_id}/clips/1/edit", json={
        "start": 8.5, "duration": 12.0, "title": "NEW SHORT HOOK",
        "effect": "pop", "framing": "pad", "caption_style": "none",
        "hook_style": "headline",
    })
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = module._read(folder)
        if job["clips"][0].get("revision_state") in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert job["clips"][0]["revision_state"] == "completed", job["clips"][0].get("revision_error")
    clip = job["clips"][0]
    assert clip["filename"] == "clip-001-v01.mp4"
    assert clip["title"] == "NEW SHORT HOOK"
    assert clip["revision_count"] == 1
    assert clip["revision_history"][0]["filename"] == "clip-001.mp4"
    assert clip["hook_style"] == "headline"
    assert observed[0][2] == "9:16"
    assert observed[0][3]["hook"].is_file()
    assert not (folder / "clipforge-clips.zip").exists()
    assert client.get(f"/api/jobs/{job_id}/clips/clip-001.mp4").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/clips/{clip['filename']}").status_code == 200
    response = client.get(f"/api/jobs/{job_id}/export")
    assert response.status_code == 200
    with zipfile.ZipFile(folder / "clipforge-clips.zip") as archive:
        assert archive.namelist() == ["clip-001-v01.mp4"]


def test_timeline_source_preview_is_scoped_to_completed_job(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = _completed_job(tmp_path)
    response = client.get(f"/api/jobs/{job_id}/source")
    assert response.status_code == 200
    assert response.content == b"original-video"
    assert response.headers["content-type"].startswith("video/mp4")
    partial = client.get(f"/api/jobs/{job_id}/source",
                         headers={"Range": "bytes=0-7"})
    assert partial.status_code == 206
    assert partial.content == b"original"
    module._patch(folder, status="failed")
    assert client.get(f"/api/jobs/{job_id}/source").status_code == 409


def test_timeline_rejects_mismatched_manual_scene_timing(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = _completed_job(tmp_path)
    job = module._read(folder)
    job["clips"][0]["storyboard"] = [
        {"start": 0, "end": 10, "text": "Original first scene"},
        {"start": 10, "end": 20, "text": "Original second scene"},
    ]
    module._write(folder, job)
    endpoint = f"/api/jobs/{job_id}/clips/1/edit"
    beats = [
        {"start": 0, "end": 10, "text": "Carefully reviewed first scene"},
        {"start": 10, "end": 20, "text": "Carefully reviewed second scene"},
    ]
    assert client.post(endpoint, json={
        "start": 5, "duration": 20, "manual_beats": beats,
        "regenerate_storyboard": True,
    }).status_code == 422
    assert client.post(endpoint, json={
        "start": 6, "duration": 20, "manual_beats": beats,
    }).status_code == 422
    assert client.post(endpoint, json={
        "start": 5, "duration": 20,
        "manual_beats": [{**beats[0], "end": 9}, beats[1]],
    }).status_code == 422
    assert client.post(endpoint, json={
        "start": 5, "duration": 20,
        "manual_beats": [{**beats[0], "text": "word " * 30}, beats[1]],
    }).status_code == 422
    assert client.post(endpoint, json={
        "start": 5, "duration": 20, "manual_beats": beats,
        "source_volume": 3,
    }).status_code == 422
    assert client.post(endpoint, json={
        "start": 5, "duration": 20, "manual_beats": beats,
        "voiceover": True,
    }).status_code == 400


def test_timeline_manual_beats_render_as_reviewed_script(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = _completed_job(tmp_path)
    job = module._read(folder)
    job["clips"][0]["storyboard"] = [
        {"start": 0, "end": 10, "text": "First scene"},
        {"start": 10, "end": 20, "text": "Second scene"},
    ]
    module._write(folder, job)
    rendered = []

    def fake_render(_source, destination, *_args, **kwargs):
        rendered.append(kwargs)
        destination.write_bytes(b"rendered" * 300)

    monkeypatch.setattr(module, "probe", lambda *_: {"duration": 60, "has_audio": False})
    monkeypatch.setattr(module, "render_clip", fake_render)
    monkeypatch.setattr(module, "render_thumbnail", lambda *_: None)
    response = client.post(f"/api/jobs/{job_id}/clips/1/edit", json={
        "start": 5, "duration": 20, "caption_style": "none",
        "manual_beats": [
            {"start": 0, "end": 10, "text": "A large transport vehicle is visible."},
            {"start": 10, "end": 20, "text": "A uniformed speaker appears on screen."},
        ],
        "source_volume": 0.55, "voice_volume": 1.2,
    })
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = module._read(folder)["clips"][0]
        if result.get("revision_state") in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert result["revision_state"] == "completed", result.get("revision_error")
    assert result["analysis_basis"] == "manual_review"
    assert result["script"] == (
        "A large transport vehicle is visible. "
        "A uniformed speaker appears on screen."
    )
    assert len(result["storyboard"]) == 2
    assert result["source_volume"] == 0.55
    assert rendered[0]["source_volume"] == 0.55
    assert rendered[0]["voice_volume"] == 1.2
    assert not result["voiceover_added"]


def test_timeline_rejects_invalid_cut_spans_and_stale_narration(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = _completed_job(tmp_path)
    endpoint = f"/api/jobs/{job_id}/clips/1/edit"
    valid = [{"start": 10, "duration": 4}, {"start": 20, "duration": 4}]
    for payload in (
        {"start": 0, "duration": 10, "segments": valid},  # Sum is eight.
        {"start": 5, "duration": 8, "segments": valid},  # Output starts at zero.
        {"start": 0, "duration": 8,
         "segments": [{"start": 59, "duration": 8}]},
        {"start": 0, "duration": 8, "segments": valid,
         "manual_beats": [{"start": 0, "end": 8, "text": "stale"}]},
        {"start": 0, "duration": 8, "segments": valid, "music_enabled": True},
    ):
        response = client.post(endpoint, json=payload)
        assert response.status_code == 422, response.text
    assert module._read(folder)["clips"][0].get("revision_state") is None


def test_timeline_allows_reviewed_narration_when_cut_order_is_unchanged(
        tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = _completed_job(tmp_path)
    job = module._read(folder)
    existing = [{"start": 10, "duration": 4}, {"start": 20, "duration": 4}]
    job["clips"][0].update(
        start=0, duration=8, segments=existing,
        storyboard=[{"start": 0, "end": 4, "text": "Scene one"},
                    {"start": 4, "end": 8, "text": "Scene two"}],
    )
    module._write(folder, job)
    monkeypatch.setattr(module.POOL, "submit", lambda *_args: None)
    request = {
        "start": 0, "duration": 8, "segments": existing,
        "manual_beats": [{"start": 0, "end": 4, "text": "Reviewed one"},
                         {"start": 4, "end": 8, "text": "Reviewed two"}],
    }
    endpoint = f"/api/jobs/{job_id}/clips/1/edit"
    changed = {**request, "segments": [{"start": 20, "duration": 4},
                                       {"start": 10, "duration": 4}]}
    assert client.post(endpoint, json=changed).status_code == 422
    assert client.post(endpoint, json=request).status_code == 202


def test_timeline_cut_worker_renders_sequence_without_overwriting_old_clip(
        tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = _completed_job(tmp_path)
    observed = {}

    def fake_sequence(source, dest, segments, **kwargs):
        observed["cuts"] = segments
        observed["sequence_has_audio"] = kwargs["has_source_audio"]
        dest.write_bytes(b"assembled-source")

    def fake_render(source, dest, start, length, *_args, **kwargs):
        observed["render"] = (source.name, start, length, kwargs)
        dest.write_bytes(b"rendered" * 250)

    monkeypatch.setattr(module, "probe", lambda *_: {"duration": 60, "has_audio": False})
    monkeypatch.setattr(module, "render_sequence", fake_sequence)
    monkeypatch.setattr(module, "render_clip", fake_render)
    monkeypatch.setattr(module, "render_thumbnail", lambda *_: None)
    response = client.post(f"/api/jobs/{job_id}/clips/1/edit", json={
        "start": 0, "duration": 8, "caption_style": "none",
        "segments": [{"start": 15, "duration": 4}, {"start": 5, "duration": 4}],
    })
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = module._read(folder)["clips"][0]
        if result.get("revision_state") in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert result["revision_state"] == "completed", result.get("revision_error")
    assert observed["cuts"] == [{"start": 15, "duration": 4},
                                {"start": 5, "duration": 4}]
    assert observed["render"][0].endswith(".sequence.mp4")
    assert observed["render"][1:3] == (0, 8)
    assert result["segments"] == observed["cuts"]
    assert result["revision_history"][0]["filename"] == "clip-001.mp4"
    assert (folder / "clips" / "clip-001.mp4").is_file()
    assert not (folder / "clips" / ".clip-001-v01.sequence.mp4").exists()


def test_music_upload_accepts_audio_only_and_rejects_invalid_media(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = _completed_job(tmp_path)
    endpoint = f"/api/jobs/{job_id}/music"
    assert client.post(endpoint, files={
        "file": ("test.mp3", b"contents", "audio/mpeg"),
    }).status_code == 400
    wrong = client.post(endpoint, data={"rights_confirmed": "true"},
                        files={"file": ("not-a-song.txt", b"text", "text/plain")})
    assert wrong.status_code == 400
    invalid = client.post(endpoint, data={"rights_confirmed": "true"},
                          files={"file": ("fake.mp3", b"bad", "audio/mpeg")})
    assert invalid.status_code == 422
    assert not module._read(folder).get("music_file")

    music = tmp_path / "short.wav"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.8", str(music),
    ], check=True, capture_output=True)
    success = client.post(endpoint, data={"rights_confirmed": "true"}, files={
        "file": ("licensed.wav", music.read_bytes(), "audio/wav"),
    })
    assert success.status_code == 201, success.text
    file_name = success.json()["music_file"]
    assert (folder / "music" / file_name).is_file()
    assert module._read(folder)["music_file"] == file_name


def test_revision_validations_no_paid_tts_without_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = _completed_job(tmp_path)
    endpoint = f"/api/jobs/{job_id}/clips/1/edit"
    assert client.post(endpoint, json={
        "start": 45, "duration": 30,
    }).status_code == 422
    assert client.post(endpoint, json={
        "start": 5, "duration": 10, "voiceover": True,
        "script": "Read this", "voiceover_approved": False,
    }).status_code == 400
    assert client.post(f"/api/jobs/{job_id}/clips/999/edit", json={
        "start": 5, "duration": 10,
    }).status_code == 404
    module._patch(folder, status="failed")
    assert client.post(endpoint, json={
        "start": 5, "duration": 10,
    }).status_code == 409


def test_restart_requeues_local_edit_but_does_not_double_bill_tts(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    _, folder = _completed_job(tmp_path)
    request = {
        "start": 5, "duration": 10, "title": "Local", "voiceover": False,
    }
    job = module._read(folder)
    job["clips"][0].update(revision_state="rendering", revision_request=request)
    module._write(folder, job)
    submitted = []
    monkeypatch.setattr(module.POOL, "submit", lambda *args: submitted.append(args))
    module.recover_interrupted_jobs()
    job = module._read(folder)
    assert len(submitted) == 1
    assert submitted[0][0] is module._edit_clip_worker
    assert job["clips"][0]["revision_state"] == "queued"

    job["clips"][0].update(
        revision_state="rendering",
        revision_request={**request, "voiceover": True},
    )
    module._write(folder, job)
    module.recover_interrupted_jobs()
    assert len(submitted) == 1
    job = module._read(folder)
    assert job["clips"][0]["revision_state"] == "failed"
    assert "Interrupted voiceover" in job["clips"][0]["revision_error"]


def test_real_ffmpeg_hook_overlay(tmp_path):
    source = tmp_path / "original.mp4"
    out = tmp_path / "revision.mp4"
    hook = tmp_path / "hook.ass"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=8:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", str(source),
    ], capture_output=True, check=True)
    assert write_hook("ONE MOMENT CHANGED EVERYTHING", 2, hook)
    render_clip(source, out, 0, 2, "9:16", hook=hook)
    assert out.stat().st_size > 400
    assert 1.5 < float(module.probe(out)["duration"]) < 2.5


def test_render_clip_audio_mixer_uses_validated_levels(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(engine.subprocess, "run", lambda args, **kwargs:
                        commands.append(args))
    source = tmp_path / "input.mp4"
    dest = tmp_path / "result.mp4"
    engine.render_clip(source, dest, 0, 3, "9:16", source_volume=0.45)
    assert commands[-1][commands[-1].index("-af") + 1] == "volume=0.450"
    engine.render_clip(source, dest, 0, 3, "9:16",
                       voiceover=tmp_path / "voice.mp3", source_volume=0.15,
                       voice_volume=1.25)
    mixer = commands[-1][commands[-1].index("-filter_complex") + 1]
    assert "[0:a:0]volume=0.150" in mixer
    assert "[1:a:0]volume=1.250" in mixer
    engine.render_clip(source, dest, 0, 3, "9:16", voiceover=tmp_path / "voice.mp3")
    default_mixer = commands[-1][commands[-1].index("-filter_complex") + 1]
    assert "[0:a:0]volume=0.180" in default_mixer
    with pytest.raises(ValueError, match="Audio volumes"):
        engine.render_clip(source, dest, 0, 3, "9:16", source_volume=float("nan"))


def test_editor_is_in_library_and_studio_contains_hook_controls():
    page = client.get("/library").text
    assert 'id="view-library"' in page
    assert 'id="hook-style"' in page
    assert 'id="settings-hook-style"' in page
    script = client.get("/assets/app.js").text
    assert "buildClipEditor" in script
    assert "/edit" in script
