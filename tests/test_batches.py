"""Multi-source jobs remain bounded, isolated and restart-resumable."""
import io

from fastapi.testclient import TestClient

from clipforge import app as module

client = TestClient(module.app)


def test_url_batch_creates_independent_jobs_with_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    submitted = []
    monkeypatch.setattr(module.POOL, "submit", lambda *args: submitted.append(args))
    urls = [
        "https://youtu.be/videoA",
        "https://www.youtube.com/watch?v=videoB",
        "https://youtu.be/videoA",  # repeated source deduplicated
    ]
    result = client.post("/api/jobs/batch", json={
        "urls": urls, "rights_confirmed": True, "max_clips": 1,
    })
    assert result.status_code == 202, result.text
    data = result.json()
    assert data["accepted"] == 2
    assert len(submitted) == 2
    batch = data["batch_id"]
    jobs = [module._read(module.job_folder(id)) for id in data["job_ids"]]
    assert all(item["batch_id"] == batch for item in jobs)
    assert [job["batch_index"] for job in jobs] == [1, 2]
    assert [job["batch_total"] for job in jobs] == [2, 2]
    assert all(job["options"]["max_clips"] == 1 for job in jobs)
    assert all("urls" not in job["options"] for job in jobs)
    assert client.get("/api/batches").json()[0]["queued"] == 2
    assert len([j for j in client.get("/api/jobs").json() if j["batch_id"] == batch]) == 2


def test_batch_rejects_bad_urls_or_missing_rights(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    assert client.post("/api/jobs/batch", json={
        "urls": ["https://youtu.be/test"],
    }).status_code == 400
    assert client.post("/api/jobs/batch", json={
        "urls": ["https://localhost/admin"], "rights_confirmed": True,
    }).status_code == 400
    assert client.post("/api/jobs/batch", json={
        "urls": [], "rights_confirmed": True,
    }).status_code == 422
    assert not list(tmp_path.glob("*/job.json"))


def test_batch_voiceover_requires_explicit_spend_approval(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    result = client.post("/api/jobs/batch", json={
        "urls": ["https://youtu.be/test"], "rights_confirmed": True,
        "narration": "narrated",
    })
    assert result.status_code == 400
    assert "voice" in result.text.lower()


def test_batch_file_upload_creates_separate_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    submitted = []
    monkeypatch.setattr(module.POOL, "submit", lambda *args: submitted.append(args))
    result = client.post("/api/uploads/batch", data={
        "rights_confirmed": "true", "max_clips": "1",
    }, files=[
        ("files", ("video1.mp4", io.BytesIO(b"media-one"), "video/mp4")),
        ("files", ("folder/video2.mov", io.BytesIO(b"media-two"), "video/quicktime")),
    ])
    assert result.status_code == 202, result.text
    out = result.json()
    assert out["accepted"] == 2
    assert len(submitted) == 2
    jobs = [module._read(module.job_folder(id)) for id in out["job_ids"]]
    assert [j["label"] for j in jobs] == ["video1.mp4", "video2.mov"]
    assert (module.job_folder(out["job_ids"][0]) / "source.mp4").read_bytes() == b"media-one"
    assert (module.job_folder(out["job_ids"][1]) / "source.mov").read_bytes() == b"media-two"
    module._patch(module.job_folder(out["job_ids"][0]), status="completed")
    module._patch(module.job_folder(out["job_ids"][1]), status="failed")
    batches = client.get("/api/batches").json()
    assert batches[0]["completed"] == 1
    assert batches[0]["failed"] == 1


def test_invalid_multi_upload_rolls_back_all_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    result = client.post("/api/uploads/batch", data={
        "rights_confirmed": "true",
    }, files=[
        ("files", ("valid.mp4", b"video", "video/mp4")),
        ("files", ("nope.exe", b"bad", "application/octet-stream")),
    ])
    assert result.status_code == 400
    assert not list(tmp_path.glob("*/job.json"))
    result = client.post("/api/uploads/batch", data={
        "rights_confirmed": "true",
    }, files=[
        ("files", ("valid.mp4", b"video", "video/mp4")),
        ("files", ("empty.mp4", b"", "video/mp4")),
    ])
    assert result.status_code == 400
    assert not list(tmp_path.glob("*/job.json"))


def test_batch_queue_limit_protects_home_server(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(module, "MAX_QUEUED_SOURCES", 1)
    result = client.post("/api/jobs/batch", json={
        "urls": ["https://youtu.be/a", "https://youtu.be/b"],
        "rights_confirmed": True,
    })
    assert result.status_code == 429
    assert not list(tmp_path.glob("*/job.json"))


def test_browser_has_multisource_input_and_batch_progress():
    page = client.get("/studio").text
    assert 'id="url-list"' in page
    assert 'id="folder"' in page
    assert 'id="activity-batches"' in page
    js = client.get("/assets/app.js").text
    assert "/api/uploads/batch" in js
    assert "/api/jobs/batch" in js
    assert "chosenVideoFiles" in js
