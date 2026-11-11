"""Publishing schedules require explicit approval and avoid accidental duplicates."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from clipforge import app as module

client = TestClient(module.app)


def _completed(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "Owned source")
    directory = folder / "clips"
    directory.mkdir(exist_ok=True)
    (directory / "clip-001.mp4").write_bytes(b"rendered-video")
    module._patch(folder, status="completed", clips=[{
        "filename": "clip-001.mp4", "number": 1, "start": 0, "duration": 15,
    }])
    monkeypatch.setattr(module, "platform_status", lambda: [
        {"id": "youtube", "configured": True, "name": "YouTube", "mode": "direct"},
        {"id": "tiktok", "configured": True, "name": "TikTok", "mode": "draft"},
        {"id": "instagram", "configured": False, "name": "Instagram", "mode": "direct"},
    ])
    return job_id, folder


def _schedule(job_id, when=None, platforms=None, approved=True):
    timestamp = when or datetime.now(timezone.utc) + timedelta(hours=1)
    return client.post("/api/jobs/" + job_id + "/schedule", json={
        "filename": "clip-001.mp4", "platforms": platforms or ["youtube", "tiktok"],
        "title": "Review before sharing", "description": "#shorts",
        "visibility": "private", "scheduled_at": timestamp.isoformat(),
        "approved": approved,
    })


def test_scheduled_publish_does_not_post_before_approval_or_due(tmp_path, monkeypatch):
    job_id, folder = _completed(tmp_path, monkeypatch)
    submitted = []
    monkeypatch.setattr(module.PUBLISH_POOL, "submit", lambda *a: submitted.append(a))
    assert _schedule(job_id, approved=False).status_code == 400
    response = _schedule(job_id)
    assert response.status_code == 202, response.text
    data = response.json()
    assert len(data["publication_ids"]) == 2
    assert not submitted
    job = module._read(folder)
    assert all(p["approved"] is True and p["state"] == "scheduled"
               for p in job["publications"])
    assert module._dispatch_due_publications() == 0
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    assert module._dispatch_due_publications(future) == 2
    assert module._dispatch_due_publications(future) == 0
    assert len(submitted) == 2
    assert {a[1] for a in submitted} == set(data["publication_ids"])


def test_schedule_validation_prevents_unconfigured_and_out_of_range(tmp_path, monkeypatch):
    job_id, folder = _completed(tmp_path, monkeypatch)
    assert _schedule(job_id, platforms=["instagram"]).status_code == 409
    assert _schedule(job_id, platforms=["youtube", "youtube"]).status_code == 422
    assert _schedule(job_id, when=datetime.now(timezone.utc) + timedelta(days=35)).status_code == 422
    naive = client.post("/api/jobs/" + job_id + "/schedule", json={
        "filename": "clip-001.mp4", "platforms": ["youtube"],
        "title": "A", "scheduled_at": "2026-12-01T09:00:00",
        "approved": True,
    })
    assert naive.status_code == 422
    assert module._read(folder)["publications"] == []


def test_cancel_scheduled_upload_is_atomic(tmp_path, monkeypatch):
    job_id, folder = _completed(tmp_path, monkeypatch)
    scheduled = _schedule(job_id)
    id = scheduled.json()["publication_ids"][0]
    resp = client.post("/api/jobs/" + job_id + "/publications/" + id + "/cancel")
    assert resp.status_code == 200
    assert resp.json()["state"] == "canceled"
    assert client.post("/api/jobs/" + job_id + "/publications/" + id + "/cancel").status_code == 409
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    submitted = []
    monkeypatch.setattr(module.PUBLISH_POOL, "submit", lambda *a: submitted.append(a))
    assert module._dispatch_due_publications(future) == 1
    assert len(submitted) == 1
    assert module._read(folder)["publications"][0]["state"] == "canceled"


def test_restart_does_not_auto_retry_unknown_provider_state(tmp_path, monkeypatch):
    job_id, folder = _completed(tmp_path, monkeypatch)
    scheduled = _schedule(job_id, platforms=["youtube"])
    id = scheduled.json()["publication_ids"][0]
    job = module._read(folder)
    job["publications"][0]["state"] = "uploading"
    module._write(folder, job)
    submitted = []
    monkeypatch.setattr(module.PUBLISH_POOL, "submit", lambda *a: submitted.append(a))
    module.recover_interrupted_publications()
    assert module._read(folder)["publications"][0]["state"] == "needs_review"
    assert module._dispatch_due_publications(datetime.now(timezone.utc) + timedelta(days=1)) == 0
    assert not submitted
    endpoint = "/api/jobs/" + job_id + "/publications/" + id + "/reapprove"
    assert client.post(endpoint, json={"approved": True}).status_code == 400
    assert client.post(endpoint, json={
        "approved": True, "duplicate_checked": True,
    }).status_code == 202
    assert len(submitted) == 1


def test_scheduled_queue_and_reusable_presets_do_not_store_credentials(tmp_path, monkeypatch):
    job_id, _ = _completed(tmp_path, monkeypatch)
    assert _schedule(job_id).status_code == 202
    queue = client.get("/api/publishing/queue")
    assert queue.status_code == 200
    assert len(queue.json()) == 2
    assert all(item["state"] == "scheduled" for item in queue.json())
    saved = client.put("/api/publishing/presets", json={
        "name": "My crosspost", "platforms": ["youtube", "tiktok"],
        "visibility": "private", "description": "#original",
    })
    assert saved.status_code == 200, saved.text
    presets = client.get("/api/publishing/presets").json()
    assert len(presets) == 1
    assert presets[0]["platforms"] == ["youtube", "tiktok"]
    assert "token" not in str(presets)


def test_publishing_schedule_controls_visible():
    html = client.get("/publishing").text
    assert 'id="publishing-scheduled-count"' in html
    js = client.get("/assets/app.js").text
    assert "Approve and schedule uploads" in js
    assert "savePreset" in js
    assert "duplicate_checked: true" in js
