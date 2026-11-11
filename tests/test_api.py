import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("CLIPFORGE_DATA_DIR", "/tmp/clipforge-test-data")
from clipforge import app as module  # noqa: E402

client = TestClient(module.app)


def test_health_and_dashboard():
    assert client.get("/api/health").json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200
    assert "From long-form to" in page.text
    assert client.get("/assets/app.js").status_code == 200


def test_requires_rights():
    response = client.post("/api/jobs", json={"url": "https://youtu.be/foo"})
    assert response.status_code == 400


def test_bad_url():
    response = client.post("/api/jobs", json={
        "url": "https://localhost/admin", "rights_confirmed": True,
    })
    assert response.status_code == 400


def test_invalid_options():
    response = client.post("/api/jobs", json={
        "url": "https://youtu.be/abc", "rights_confirmed": True,
        "max_clips": 999999,
    })
    assert response.status_code == 422


def test_unknown_job():
    assert client.get("/api/jobs/" + "1" * 32).status_code == 404
    assert client.get("/api/jobs/not-a-valid-id").status_code == 404


def test_upload_and_export(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    source = tmp_path / "source.mp4"
    import subprocess

    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            "testsrc2=size=160x90:rate=8:duration=3.5", "-c:v", "libx264",
            "-preset", "ultrafast", str(source),
        ],
        capture_output=True,
        check=True,
    )
    with source.open("rb") as stream:
        response = client.post("/api/uploads", data={
            "clip_seconds": "10", "max_clips": "5", "rights_confirmed": "true",
        }, files={"file": ("test.mp4", stream, "video/mp4")})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.2)
    assert job["status"] == "completed", job.get("error")
    assert len(job["clips"]) == 1
    name = job["clips"][0]["filename"]
    assert client.get(f"/api/jobs/{job_id}/clips/{name}").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/clips/../../job.json").status_code != 200
    exported = client.get(f"/api/jobs/{job_id}/export")
    assert exported.status_code == 200
    assert exported.content[:2] == b"PK"
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert not (Path(tmp_path) / job_id).exists()


def test_ai_editing_upload_end_to_end_with_stub_models(tmp_path, monkeypatch):
    """Proves the real API, captions and FFmpeg chain works without model downloads."""
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    source = tmp_path / "ai-source.mp4"
    import subprocess

    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=160x90:rate=10:duration=3.5", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=3.5", "-shortest", "-c:v", "libx264",
        "-preset", "ultrafast", "-c:a", "aac", str(source),
    ], capture_output=True, check=True)
    transcript = [{
        "start": 0, "end": 3, "text": "An exciting discovery!",
        "words": [
            {"start": 0.5, "end": 0.9, "text": "An"},
            {"start": 0.9, "end": 1.7, "text": "exciting"},
            {"start": 1.7, "end": 2.5, "text": "discovery!"},
        ],
    }]
    monkeypatch.setattr(module, "transcribe", lambda *args: transcript)
    monkeypatch.setattr(module, "select_ai_highlights", lambda *args: [{
        "start": 0, "duration": 3.2, "score": 9.1,
        "title": "Big discovery", "reason": "Strong hook",
    }])
    with source.open("rb") as stream:
        response = client.post("/api/uploads", data={
            "clip_seconds": "15", "max_clips": "5", "rights_confirmed": "true",
            "selection_mode": "ai", "caption_style": "bold", "effect": "pop",
            "framing": "crop",
        }, files={"file": ("source.mp4", stream, "video/mp4")})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)
    assert job["status"] == "completed", job.get("error")
    assert len(job["clips"]) == 1
    assert job["clips"][0]["captions_added"] is True
    assert job["clips"][0]["title"] == "Big discovery"
    assert client.get(f"/api/jobs/{job_id}/export").status_code == 200
