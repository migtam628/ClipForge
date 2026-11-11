"""No live social publishing in CI: API calls are mocked and never post content."""
import json

from fastapi.testclient import TestClient

from clipforge import publishing
from clipforge import app as app_module


def test_platform_status_does_not_leak_credentials(monkeypatch):
    monkeypatch.setenv("CLIPFORGE_TIKTOK_ACCESS_TOKEN", "never-log-this-secret")
    statuses = publishing.platform_status()
    assert next(s for s in statuses if s["id"] == "tiktok")["configured"] is True
    assert "never-log-this-secret" not in json.dumps(statuses)
    assert next(s for s in statuses if s["id"] == "tiktok")["mode"] == "draft"


def test_submission_requires_review_and_completed_own_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    client = TestClient(app_module.app)
    options = app_module.Options()
    job_id, folder = app_module._create("upload", options, "fixture")
    path = folder / "clips"
    path.mkdir()
    (path / "clip-001.mp4").write_bytes(b"not actually a video")
    app_module._patch(folder, status="completed", clips=[{"filename": "clip-001.mp4"}])
    body = {"filename": "clip-001.mp4", "platform": "youtube", "title": "Test"}
    assert client.post(f"/api/jobs/{job_id}/publish", json=body).status_code == 400
    assert client.post(f"/api/jobs/{job_id}/publish", json={
        **body, "filename": "source.mp4", "approved": True,
    }).status_code == 404
    assert client.post(f"/api/jobs/{job_id}/publish", json={
        **body, "approved": True,
    }).status_code == 409
    assert client.get("/api/publishing/platforms").status_code == 200
    assert client.get("/api/ai/status").status_code == 200


def test_tiktok_inbox_upload_not_mislabeled_as_published(tmp_path, monkeypatch):
    source = tmp_path / "render.mp4"
    source.write_bytes(b"MP4" * 100)
    monkeypatch.setenv("CLIPFORGE_TIKTOK_ACCESS_TOKEN", "placeholder")
    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs))
        return {"data": {"upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=1",
                         "publish_id": "draft-123"}, "error": {"code": "ok"}}

    monkeypatch.setattr(publishing, "_json_request", fake_request)
    monkeypatch.setattr(publishing, "_upload_file", lambda *a, **kw: calls.append((a, kw)))
    result = publishing.publish("tiktok", source, "Video", "Description", "private")
    assert result["state"] == "sent_to_inbox"
    assert result["remote_id"] == "draft-123"
    assert calls[0][1]["payload"]["source_info"]["source"] == "FILE_UPLOAD"


def test_instagram_resumable_upload_and_publish(tmp_path, monkeypatch):
    source = tmp_path / "reel.mp4"
    source.write_bytes(b"FAKE VIDEO")
    monkeypatch.setenv("CLIPFORGE_INSTAGRAM_TOKEN", "not-a-real-token")
    monkeypatch.setenv("CLIPFORGE_INSTAGRAM_USER_ID", "12345")
    calls = []

    def fake_api(url, **kwargs):
        calls.append(url)
        if url.endswith("/media"):
            return {"id": "54321"}
        if "fields=status_code,status" in url:
            return {"status_code": "FINISHED"}
        if url.endswith("/media_publish"):
            return {"id": "98765"}
        raise AssertionError(url)

    monkeypatch.setattr(publishing, "_json_request", fake_api)
    monkeypatch.setattr(publishing, "_upload_file", lambda *a, **kw: calls.append(a[0]))
    output = publishing.publish("instagram", source, "Title", "#clip", "private")
    assert output["state"] == "published"
    assert calls[0].endswith("/media")
    assert "rupload.facebook.com/ig-api-upload" in calls[1]
    assert calls[-1].endswith("/media_publish")


def test_reject_untrusted_upload_destination(tmp_path):
    file = tmp_path / "test.mp4"
    file.write_bytes(b"file")
    try:
        publishing._upload_file("https://evil.example/video", file, {}, allow="tiktokapis.com")
    except RuntimeError as exc:
        assert "unexpected" in str(exc)
    else:
        raise AssertionError("Upload URL must be verified")


def test_youtube_refresh_upload_defaults_private(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video-data")
    for name in publishing.SOCIALS["youtube"]["required"]:
        monkeypatch.setenv(name, "placeholder")
    calls = []
    monkeypatch.setattr(publishing, "_youtube_token", lambda: "fake-access-token")

    class Headers:
        @staticmethod
        def get(name):
            return "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=abc"

    class UploadSession:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_open(request, timeout=0):
        calls.append(json.loads(request.data))
        return UploadSession()

    monkeypatch.setattr(publishing, "urlopen", fake_open)
    monkeypatch.setattr(publishing, "_upload_file", lambda *args, **kwargs: {"id": "id123"})
    uploaded = publishing.publish("youtube", clip, "Short", "A short video", "private")
    assert calls[0]["status"]["privacyStatus"] == "private"
    assert uploaded["state"] == "submitted"
    assert uploaded["url"].endswith("id123")


def test_facebook_page_reel_upload_has_distinct_submitted_state(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video-data")
    monkeypatch.setenv("CLIPFORGE_FACEBOOK_PAGE_ACCESS_TOKEN", "placeholder")
    calls = []

    def mocked_api(url, **kwargs):
        calls.append(kwargs.get("form", {}))
        if calls[-1]["upload_phase"] == "start":
            return {"video_id": "12345", "upload_url":
                    "https://rupload.facebook.com/video-upload/v24.0/12345"}
        return {"success": True}

    monkeypatch.setattr(publishing, "_json_request", mocked_api)
    monkeypatch.setattr(publishing, "_upload_file", lambda *args, **kwargs: None)
    published = publishing.publish("facebook", clip, "My Reel", "Description", "private")
    assert calls[0]["upload_phase"] == "start"
    assert calls[-1]["video_state"] == "PUBLISHED"
    assert published["state"] == "submitted"


def test_no_social_credentials_or_unsupported_platform(tmp_path, monkeypatch):
    file = tmp_path / "clip.mp4"
    file.write_bytes(b"abc")
    for req in publishing.SOCIALS["youtube"]["required"]:
        monkeypatch.delenv(req, raising=False)
    import pytest

    with pytest.raises(RuntimeError, match="not connected"):
        publishing.publish("youtube", file, "title", "desc", "private")
    with pytest.raises(ValueError, match="Unknown"):
        publishing.publish("unsupported", file, "title", "desc", "private")
