"""Restart-safe recovery and resumable AI scoring tests."""
import json
from urllib.error import URLError

from fastapi.testclient import TestClient

from clipforge import ai
from clipforge import app as module


client = TestClient(module.app)


class FakePool:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))


def test_resume_endpoint_keeps_existing_source(tmp_path, monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(module, "POOL", pool)
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "test.mp4")
    source = folder / "source.mp4"
    source.write_bytes(b"original media")
    module._patch(folder, status="failed", error="transient network problem")
    response = client.post(f"/api/jobs/{job_id}/resume")
    assert response.status_code == 202
    assert response.json()["resume_count"] == 1
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 409
    assert module._read(folder)["status"] == "queued"
    assert module._read(folder)["error"] is None
    assert source.read_bytes() == b"original media"
    assert len(pool.calls) == 1


def test_auto_recover_interrupted_but_not_failed_jobs(tmp_path, monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(module, "POOL", pool)
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    _, interrupted = module._create("upload", module.Options(), "interrupted.mp4")
    _, failed = module._create("upload", module.Options(), "failed.mp4")
    module._patch(interrupted, status="rendering", progress=65)
    module._patch(failed, status="failed", error="bad config")
    module.recover_interrupted_jobs()
    assert module._read(interrupted)["status"] == "queued"
    assert module._read(interrupted)["progress"] == 65
    assert module._read(interrupted)["resume_count"] == 1
    assert module._read(failed)["status"] == "failed"
    assert len(pool.calls) == 1


def test_resume_reuses_source_transcript_and_finished_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    opts = module.Options(selection_mode="ai", clip_seconds=30, max_clips=2)
    _, folder = module._create("upload", opts, "source.mp4")
    (folder / "source.mp4").write_bytes(b"original video")
    monkeypatch.setattr(module, "probe", lambda _p: {"duration": 65, "has_audio": True})
    monkeypatch.setattr(module, "audio_energy", lambda *_: [0.0] * 33)
    counts = {"transcribe": 0, "scoring": 0, "render": 0}

    def transcribe(*_args):
        counts["transcribe"] += 1
        return [{"start": 0, "end": 30, "text": "Interesting moment", "words": []}]

    def select(*_args):
        counts["scoring"] += 1
        return [{"start": 0, "duration": 30, "score": 9.0, "title": "A"},
                {"start": 30, "duration": 30, "score": 8.0, "title": "B"}]

    def render(_src, dest, *_args, **_kwargs):
        counts["render"] += 1
        if counts["render"] == 2:
            raise RuntimeError("simulated FFmpeg crash")
        dest.write_bytes(b"video-content" * 100)

    monkeypatch.setattr(module, "transcribe", transcribe)
    monkeypatch.setattr(module, "select_ai_highlights", select)
    monkeypatch.setattr(module, "render_clip", render)
    monkeypatch.setattr(module, "render_thumbnail",
                        lambda _src, dst: dst.write_bytes(b"jpg"))

    module._process(folder)
    assert module._read(folder)["status"] == "failed"
    assert len(module._read(folder)["clips"]) == 1
    assert counts == {"transcribe": 1, "scoring": 1, "render": 2}
    module._process(folder)
    assert module._read(folder)["status"] == "completed"
    assert len(module._read(folder)["clips"]) == 2
    assert counts == {"transcribe": 1, "scoring": 1, "render": 3}
    assert (folder / "source.mp4").read_bytes() == b"original video"


def test_scoring_checkpoint_skips_completed_batches(tmp_path, monkeypatch):
    checkpoint = tmp_path / "ai-scores.json"
    transcript = [{"start": 0.0, "end": 300.0, "text": "Compelling story", "words": []}]
    monkeypatch.setenv("CLIPFORGE_AI_BATCH_SIZE", "4")
    calls = []
    fail_once = {"done": False}

    def answer(batch):
        calls.append([w["id"] for w in batch])
        if batch[0]["id"] == 4 and not fail_once["done"]:
            fail_once["done"] = True
            raise RuntimeError("temporary model timeout")
        return [{"id": w["id"], "score": 9, "title": "Good", "reason": "Hook"}
                for w in batch]

    monkeypatch.setattr(ai, "_ask_ollama", answer)
    try:
        ai.select_ai_highlights(transcript, 300, 30, 5, checkpoint_path=checkpoint)
    except RuntimeError as exc:
        assert "timeout" in str(exc)
    else:
        raise AssertionError("expected first scoring attempt to fail")
    persisted = json.loads(checkpoint.read_text())
    assert len(persisted["scores"]) == 4
    assert calls[:2] == [[0, 1, 2, 3], [4, 5, 6, 7]]
    clips = ai.select_ai_highlights(transcript, 300, 30, 5, checkpoint_path=checkpoint)
    assert len(clips) == 5
    assert calls[2] == [4, 5, 6, 7]
    assert [0, 1, 2, 3] not in calls[1:]


def test_ollama_retries_transient_network_error(monkeypatch):
    monkeypatch.setenv("CLIPFORGE_OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("CLIPFORGE_AI_RETRIES", "3")
    monkeypatch.setattr(ai.time, "sleep", lambda _seconds: None)
    calls = {"count": 0}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return json.dumps({"response": json.dumps({
                "scores": [{"id": 0, "score": 8, "title": "Nice", "reason": "Good"}],
            })}).encode()

    def call(_request, timeout):
        assert timeout >= 240
        calls["count"] += 1
        if calls["count"] == 1:
            raise URLError("temporary network outage")
        return Response()

    monkeypatch.setattr(ai, "urlopen", call)
    result = ai._ask_ollama([{"id": 0, "text": "Interesting excerpt"}])
    assert result[0]["score"] == 8
    assert calls["count"] == 2
