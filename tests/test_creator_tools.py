"""Nexus-inspired creator controls: grounded post copy, voice markers, brand kits."""
import json
import subprocess

from fastapi.testclient import TestClient

from clipforge import ai, creator_tools
from clipforge import app as module
from clipforge.captions import write_brand_sticker
from clipforge.engine import probe, render_clip

client = TestClient(module.app)


def transcript():
    return [
        {"start": 0, "end": 8, "text": "A funny moment and a surprising response.",
         "words": []},
        {"start": 15, "end": 20, "text": "Clip this! That was an amazing play.",
         "words": [
             {"start": 15.1, "end": 15.5, "text": "Clip"},
             {"start": 15.5, "end": 15.8, "text": "this!"},
         ]},
        {"start": 90, "end": 95, "text": "Unrelated content elsewhere", "words": []},
    ]


def test_spoken_markers_use_word_time_and_not_live_audio():
    assert creator_tools.detect_voice_markers(transcript(), 30) == [15.1]
    windows = [
        {"id": 1, "start": 0, "duration": 10},
        {"id": 2, "start": 10, "duration": 10},
    ]
    assert creator_tools.marked_window_ids(windows, [15.1]) == {2}
    assert creator_tools.detect_voice_markers([], 30) == []


def test_prompt_guides_scoring_and_marker_can_rescue_low_score(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPFORGE_AI_MODEL", "llama3.2:3b")
    captured = []

    def scorer(batch, focus="", niche="general"):
        captured.append((focus, niche))
        return [{
            "id": window["id"], "score": 2,
            "title": "A marked clip", "reason": "Spoken marker",
        } for window in batch]

    monkeypatch.setattr(ai, "_ask_ollama", scorer)
    picks = ai.select_ai_highlights(
        transcript(), 30, 10, 3, None, tmp_path / "scores.json",
        "funny reactions", "gaming", True,
    )
    assert any(p["voice_marker"] for p in picks)
    assert all(p["creator_niche"] == "gaming" for p in picks)
    assert captured and all(item == ("funny reactions", "gaming") for item in captured)
    assert json.loads((tmp_path / "scores.json").read_text())["signature"]


def test_local_post_copy_is_truthful_editable_and_cached(monkeypatch, tmp_path):
    from clipforge import creator_tools as module_tools

    prompts = []

    def mocked(body, **kw):
        prompts.append(body["prompt"])
        return json.dumps({
            "title": "A surprising reaction",
            "description": "The speaker shares an unexpected reaction.",
            "hashtags": ["#gaming", "#clips", "#gaming", "unsafe", "#shorts"],
        })

    monkeypatch.setattr(module_tools, "_ollama", mocked)
    clip = {
        "start": 0, "duration": 30, "title": "Reaction highlight",
        "storyboard": [{"text": "The reaction surprises the speaker."}],
    }
    path = tmp_path / "copy.json"
    first = module_tools.suggest_post_copy(clip, transcript(), "gaming", "en", path)
    assert first["title"] == "A surprising reaction"
    assert first["hashtags"] == ["#gaming", "#clips", "#shorts"]
    assert first["needs_review"] is True
    assert "Unrelated content" not in prompts[0]
    assert module_tools.suggest_post_copy(
        clip, transcript(), "gaming", "en", path,
    ) == first
    assert len(prompts) == 1


def test_creator_profiles_and_post_copy_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    response = client.put("/api/creator/profiles", json={
        "name": "Travel Reels", "content_niche": "travel",
        "clip_focus": "Unexpected destinations", "brand_text": "@signaltravel",
    })
    assert response.status_code == 200, response.text
    assert client.get("/api/creator/profiles").json()[0]["name"] == "Travel Reels"
    assert client.put("/api/creator/profiles", json={
        "name": "Travel Reels", "content_niche": "gaming",
    }).status_code == 200
    assert len(client.get("/api/creator/profiles").json()) == 1
    assert client.put("/api/settings", json={
        "clip_focus": "x" * 401,
    }).status_code == 422

    jobid, folder = module._create(
        "upload", module.Options(content_niche="gaming"), "my video",
    )
    module._patch(folder, status="completed", clips=[{
        "number": 1, "filename": "clip-001.mp4", "start": 0,
        "duration": 20, "storyboard": [{"text": "The player celebrates."}],
    }])
    submitted = []
    monkeypatch.setattr(module.POOL, "submit", lambda *args: submitted.append(args))
    response = client.post(f"/api/jobs/{jobid}/clips/1/post-copy")
    assert response.status_code == 202, response.text
    assert len(submitted) == 1
    assert client.post(f"/api/jobs/{jobid}/clips/1/post-copy").status_code == 409

    monkeypatch.setattr(module, "suggest_post_copy", lambda *args: {
        "title": "A gaming highlight", "description": "From this clip",
        "hashtags": ["#gaming"], "source": "local_ai_suggestion",
        "needs_review": True,
    })
    module._post_copy_worker(folder, 1)
    clip = module._read(folder)["clips"][0]
    assert clip["post_copy_state"] == "completed"
    assert clip["post_copy"]["title"] == "A gaming highlight"


def test_brand_overlay_is_sanitized_and_renders(tmp_path):
    overlay = tmp_path / "brand.ass"
    assert write_brand_sticker(r"@channel {override}\N", 2, overlay)
    text = overlay.read_text()
    assert "Dialogue:" in text
    assert "{override}" not in text
    assert not write_brand_sticker("", 2, overlay)

    source = tmp_path / "original.mp4"
    result = tmp_path / "final.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=8:duration=1.5",
        "-c:v", "libx264", "-preset", "ultrafast", str(source),
    ], check=True, capture_output=True)
    assert write_brand_sticker("@travel", 1.5, overlay)
    render_clip(source, result, 0, 1.5, "9:16", brand=overlay)
    assert result.stat().st_size > 1000
    assert probe(result)["duration"] >= 1.4


def test_creator_ui_has_profiles_and_post_copy_action():
    page = client.get("/studio").text
    assert 'id="clip-focus"' in page
    assert 'id="content-niche"' in page
    assert 'id="brand-text"' in page
    assert 'id="use-voice-markers"' in page
    assert 'id="creator-profile-select"' in page
    script = client.get("/assets/app.js").text
    assert "/api/creator/profiles" in script
    assert "Draft post copy from video" in script



def test_single_upload_keeps_creator_brief_and_brand(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(module.POOL, "submit", lambda *args: calls.append(args))
    response = client.post("/api/uploads", data={
        "rights_confirmed": "true", "clip_focus": "A surprise ending",
        "content_niche": "travel", "use_voice_markers": "false",
        "brand_text": "@travelchannel", "selection_mode": "ai",
    }, files={"file": ("test.mp4", b"test-video", "video/mp4")})
    assert response.status_code == 202, response.text
    job = module._read(module.job_folder(response.json()["id"]))
    assert job["options"]["clip_focus"] == "A surprise ending"
    assert job["options"]["content_niche"] == "travel"
    assert job["options"]["use_voice_markers"] is False
    assert job["options"]["brand_text"] == "@travelchannel"
    assert len(calls) == 1
