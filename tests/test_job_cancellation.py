"""Cancel/delete tests prove workers never write to a removed job directory."""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CLIPFORGE_DATA_DIR", "/tmp/clipforge-test-data")
from clipforge import app as module  # noqa: E402


@pytest.fixture
def local_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        yield pool


def create_job():
    job_id, folder = module._create("url", module.Options(), "Test source")
    return job_id, folder


def wait_for_deletion(folder):
    deadline = time.monotonic() + 3
    while folder.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not folder.exists()


def wait_for_status(folder, status):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        saved = module._read(folder)
        if saved["status"] == status:
            return saved
        time.sleep(0.01)
    pytest.fail(f"Job did not reach {status}")


def test_cancel_queued_work_and_keep_checkpoints(local_jobs):
    client = TestClient(module.app)
    holding = threading.Event()
    release = threading.Event()
    started = []

    def occupying_worker():
        holding.set()
        assert release.wait(5)

    local_jobs.submit(occupying_worker)
    assert holding.wait(3)
    job_id, folder = create_job()
    try:
        module._submit_job_task(folder, local_jobs, lambda: started.append(True))
        (folder / "transcript.json").write_text('[]', encoding="utf-8")
        response = client.post(f"/api/jobs/{job_id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "canceled"
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
        assert client.post(f"/api/jobs/{job_id}/resume").status_code == 409
        assert folder.is_dir()
        assert (folder / "transcript.json").is_file()
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "canceled"
    finally:
        release.set()
    assert started == []


def test_delete_queued_job_cancels_future_before_file_cleanup(local_jobs):
    client = TestClient(module.app)
    holding = threading.Event()
    release = threading.Event()
    started = []

    def occupying_worker():
        holding.set()
        assert release.wait(5)

    local_jobs.submit(occupying_worker)
    assert holding.wait(3)
    job_id, folder = create_job()
    try:
        module._submit_job_task(folder, local_jobs, lambda: started.append(True))
        response = client.delete(f"/api/jobs/{job_id}")
        assert response.status_code == 204
        assert not folder.exists()
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
    finally:
        release.set()
    assert started == []


def test_running_job_cancels_without_overwriting_status_then_deletes(local_jobs):
    client = TestClient(module.app)
    started = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    job_id, folder = create_job()

    def ongoing_processing():
        module._stage(folder, "analyzing", 18)
        started.set()
        assert release.wait(5)
        try:
            module._patch(folder, status="completed", progress=100)
        except module.JobCancelled:
            stopped.set()

    future = module._submit_job_task(folder, local_jobs, ongoing_processing)
    assert started.wait(3)
    cancel = client.post(f"/api/jobs/{job_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "canceling"
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "canceling"
    assert client.delete(f"/api/jobs/{job_id}").status_code == 202
    assert folder.is_dir(), "Active worker must keep its files until it finishes"
    assert all(job["id"] != job_id for job in client.get("/api/jobs").json())
    release.set()
    future.result(timeout=5)
    assert stopped.is_set()
    wait_for_deletion(folder)


def test_real_processing_worker_stops_after_inflight_probe(local_jobs, monkeypatch):
    client = TestClient(module.app)
    started = threading.Event()
    release = threading.Event()
    job_id, folder = create_job()
    source = folder / "source.mp4"
    source.write_bytes(b"fixture")

    def delayed_probe(_source):
        started.set()
        assert release.wait(5)
        return {"has_audio": False, "duration": 30}

    monkeypatch.setattr(module, "probe", delayed_probe)
    future = module._submit_job_task(
        folder, local_jobs, module._process, folder, source, None,
    )
    assert started.wait(3)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    release.set()
    future.result(timeout=5)
    saved = wait_for_status(folder, "canceled")
    assert saved["status"] == "canceled"
    assert saved["progress"] == 10
    assert saved["error"] is None


def test_delete_during_external_publish_defers_cleanup(local_jobs, monkeypatch):
    client = TestClient(module.app)
    started = threading.Event()
    release = threading.Event()
    job_id, folder = create_job()
    job = module._read(folder)
    job["status"] = "completed"
    job["publications"] = [{"id": "pub-1", "state": "queued"}]
    module._write(folder, job)
    clip = folder / "clip.mp4"
    clip.write_bytes(b"media")

    def remote_publish(*_args):
        started.set()
        assert release.wait(5)
        assert clip.read_bytes() == b"media"
        return {"state": "published"}

    monkeypatch.setattr(module, "publish", remote_publish)
    future = module._submit_job_task(
        folder, local_jobs, module._publish_worker,
        folder, "pub-1", "youtube", clip, "Title", "Description", "private",
    )
    assert started.wait(3)
    try:
        assert client.delete(f"/api/jobs/{job_id}").status_code == 202
        assert clip.is_file()
    finally:
        release.set()
    future.result(timeout=5)
    wait_for_deletion(folder)


def test_unfinished_deletion_cleans_up_on_restart(local_jobs):
    job_id, folder = create_job()
    job = module._read(folder)
    job["status"] = "deleting"
    module._write(folder, job)
    module.recover_interrupted_jobs()
    assert not folder.exists()
    assert job_id not in module.JOB_FUTURES


def test_cancel_requires_active_job_and_ui_has_controls(local_jobs):
    client = TestClient(module.app)
    job_id, folder = create_job()
    data = module._read(folder)
    data["status"] = "completed"
    module._write(folder, data)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409
    for location in ("/activity", "/library"):
        assert client.get(location).status_code == 200
    script = client.get("/assets/app.js").text
    assert "Cancel processing" in script
    assert "Cancel job" in script
    assert "deleteSourceJob" in script
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204


def test_cancel_is_persistent_and_not_requeued(local_jobs, monkeypatch):
    job_id, folder = create_job()
    saved = module._read(folder)
    saved["status"] = "canceled"
    module._write(folder, saved)
    submitted = []
    monkeypatch.setattr(module.POOL, "submit", lambda *args: submitted.append(args))
    module.recover_interrupted_jobs()
    assert json.loads((folder / "job.json").read_text())["status"] == "canceled"
    assert submitted == []
    assert job_id not in module.JOB_FUTURES
