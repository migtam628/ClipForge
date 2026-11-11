"""Non-destructive Clip Library editor and safe-zone hook regression tests."""
from __future__ import annotations

import json
import subprocess
import time
import zipfile

from fastapi.testclient import TestClient

from clipforge import app as module
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


def test_editor_is_in_library_and_studio_contains_hook_controls():
    page = client.get("/library").text
    assert 'id="view-library"' in page
    assert 'id="hook-style"' in page
    assert 'id="settings-hook-style"' in page
    script = client.get("/assets/app.js").text
    assert "buildClipEditor" in script
    assert "/edit" in script
