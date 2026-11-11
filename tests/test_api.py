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
    assert "One video." in page.text
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
