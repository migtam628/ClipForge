"""Page navigation, saved defaults, AI commentary and mixed audio regression tests."""
import json
import subprocess

from fastapi.testclient import TestClient

from clipforge import app as module, narration
from clipforge.captions import write_narration_captions
from clipforge.engine import render_clip

client = TestClient(module.app)


def test_all_workspace_pages_are_navigable():
    for route in ["/", "/studio", "/library", "/publishing", "/activity", "/settings"]:
        response = client.get(route)
        assert response.status_code == 200
        assert "ClipForge" in response.text
        assert 'href="/settings"' in response.text
    assert "Custom amount" in client.get("/studio").text
    assert "Generate AI script" in client.get("/studio").text


def test_settings_defaults_are_persisted_and_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["defaults"]["max_clips"] == 5
    assert response.json()["defaults"]["narration"] == "none"

    saved = {
        "max_clips": 1, "clip_seconds": 30, "narration": "script",
        "narration_style": "documentary", "narration_language": "es",
    }
    response = client.put("/api/settings", json=saved)
    assert response.status_code == 200
    assert response.json()["defaults"]["max_clips"] == 1
    assert response.json()["defaults"]["narration_language"] == "es"
    assert json.loads((tmp_path / "settings.json").read_text())["max_clips"] == 1
    assert client.get("/api/settings").json()["defaults"]["max_clips"] == 1
    assert client.put("/api/settings", json={"max_clips": 0}).status_code == 422
    assert client.put("/api/settings", json={"max_clips": 101}).status_code == 422
    assert client.put("/api/settings", json={"max_clips": 5}).status_code == 200
    assert client.get("/api/settings").json()["defaults"]["max_clips"] == 5


def test_script_prompt_is_grounded_and_checkpointed(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_OLLAMA_URL", "http://ollama:11434")
    checkpoint = tmp_path / "narration.json"
    transcript = [
        {"start": 5, "end": 16, "text": "The scientist identified a new kind of frog"},
        {"start": 70, "end": 75, "text": "Unrelated segment outside this clip"},
    ]
    prompts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _size):
            return json.dumps({"response": json.dumps({
                "script": "Researchers in this clip describe identifying a new kind of frog.",
            })}).encode()

    def urlopen(request, timeout):
        assert timeout >= 60
        prompts.append(json.loads(request.data.decode())["prompt"])
        return Response()

    monkeypatch.setattr(narration, "urlopen", urlopen)
    script = narration.build_script(transcript, 0, 30, "documentary", "en", checkpoint)
    assert "frog" in script
    assert len(prompts) == 1
    assert "new kind of frog" in prompts[0]
    assert "Unrelated segment" not in prompts[0]
    assert "Do NOT invent facts" in prompts[0]
    assert narration.build_script(transcript, 0, 30, "documentary", "en", checkpoint) == script
    assert len(prompts) == 1


def test_external_tts_is_optional_and_secrets_not_exposed(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPFORGE_ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("CLIPFORGE_ELEVENLABS_VOICE_ID", raising=False)
    assert narration.narration_status()["elevenlabs_configured"] is False
    try:
        narration.require_voice_config()
    except RuntimeError as exc:
        assert "CLIPFORGE_ELEVENLABS_API_KEY" in str(exc)
    else:
        raise AssertionError("voiceover must require configured credentials")
    monkeypatch.setenv("CLIPFORGE_ELEVENLABS_API_KEY", "private-test-api-secret")
    monkeypatch.setenv("CLIPFORGE_ELEVENLABS_VOICE_ID", "12345678ValidVoice")
    monkeypatch.setattr(narration, "audio_duration", lambda _path: 2.0)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _size):
            return b"fake-mp3-payload" * 30

    calls = []

    def fake_call(request, timeout):
        calls.append((request.full_url, json.loads(request.data.decode())))
        assert timeout > 30
        return Response()

    monkeypatch.setattr(narration, "urlopen", fake_call)
    path = tmp_path / "voice.mp3"
    narration.synthesize("This is the narrator.", path)
    assert path.exists()
    assert calls[0][0].startswith("https://api.elevenlabs.io/v1/text-to-speech/")
    assert calls[0][1]["text"] == "This is the narrator."
    narration.synthesize("This is the narrator.", path)
    assert len(calls) == 1  # audio checkpoint prevents repeat API billing
    response = client.get("/api/settings")
    assert "private-test-api-secret" not in response.text
    assert "12345678ValidVoice" not in response.text


def test_narrated_job_records_script_and_voiceover(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    opts = module.Options(narration="narrated", caption_style="bold", max_clips=1)
    _, folder = module._create("upload", opts, "sample.mp4")
    (folder / "source.mp4").write_bytes(b"source")
    monkeypatch.setattr(module, "probe", lambda _path: {
        "has_audio": True, "duration": 30.0,
    })
    monkeypatch.setattr(module, "audio_energy", lambda *_: [0.0] * 15)
    monkeypatch.setattr(module, "transcribe", lambda *_: [
        {"start": 0, "end": 30, "text": "A surprising discovery", "words": []},
    ])
    monkeypatch.setattr(module, "select_highlights", lambda *_: [
        {"start": 0, "duration": 30, "score": 2.0},
    ])
    monkeypatch.setattr(module, "require_voice_config", lambda: ("demo", "voice"))
    monkeypatch.setattr(module, "build_script", lambda *_: "What a fascinating discovery.")
    def fake_voice(_script, path):
        path.write_bytes(b"some-narrator-audio")
        return path
    monkeypatch.setattr(module, "synthesize", fake_voice)
    monkeypatch.setattr(module, "audio_duration", lambda _path: 5.0)
    monkeypatch.setattr(module, "write_narration_captions",
                        lambda _s, _d, _l, _style, path: path.write_text("captions") or True)
    recorded = []

    def fake_render(_src, output, *_args, **kwargs):
        recorded.append(kwargs)
        output.write_bytes(b"generated-video" * 100)

    monkeypatch.setattr(module, "render_clip", fake_render)
    monkeypatch.setattr(module, "render_thumbnail",
                        lambda _src, dst: dst.write_bytes(b"jpg"))
    module._process(folder)
    job = module._read(folder)
    assert job["status"] == "completed", job["error"]
    clip = job["clips"][0]
    assert clip["script"] == "What a fascinating discovery."
    assert clip["voiceover_added"] is True
    assert recorded[0]["voiceover"].name.endswith(".mp3")
    assert recorded[0]["has_source_audio"] is True
    assert clip["captions_added"] is True


def test_ffmpeg_narration_mix_and_caption_overlay(tmp_path):
    video = tmp_path / "original.mp4"
    voice = tmp_path / "voice.mp3"
    output = tmp_path / "narrated.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", str(video),
    ], check=True, capture_output=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=1.5",
        "-c:a", "libmp3lame", str(voice),
    ], check=True, capture_output=True)
    captions = tmp_path / "commentary.ass"
    assert write_narration_captions(
        "Here's what made this moment so fascinating", 1.5, 2.0, "bold", captions,
    )
    assert "MOMENT" in captions.read_text()
    render_clip(video, output, 0, 2, "9:16", voiceover=voice, captions=captions)
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of",
        "json", str(output),
    ], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    assert any(stream["codec_type"] == "video" for stream in data["streams"])
    assert any(stream["codec_type"] == "audio" for stream in data["streams"])
    assert float(data["format"]["duration"]) >= 1.8
