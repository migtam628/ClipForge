"""Model-independent AI logic and real media processing integration tests."""
import subprocess

import pytest

from clipforge import ai
from clipforge.captions import write_captions
from clipforge.engine import probe, render_clip


def _segments():
    return [{"start": 0, "end": 8, "text": "This is a good punchline!",
             "words": [{"start": 0.5, "end": 0.9, "text": "Hello"},
                       {"start": 0.9, "end": 1.3, "text": "world!"}]},
            {"start": 31, "end": 39, "text": "An interesting discovery."}]


def test_ai_prefers_meaningful_scores_and_preserves_boundaries(monkeypatch):
    monkeypatch.setattr(ai, "_ask_ollama", lambda batch: [
        {"id": w["id"], "score": 9 if w["id"] == 1 else 4,
         "title": "Interesting moment", "reason": "Clear hook"} for w in batch
    ])
    clips = ai.select_ai_highlights(_segments(), 65, 30, 10)
    assert len(clips) == 1
    assert clips[0]["start"] == 30
    assert clips[0]["score"] == 9
    assert clips[0]["title"] == "Interesting moment"


def test_ai_ignores_hallucinated_ids_and_bad_scores(monkeypatch):
    monkeypatch.setattr(ai, "_ask_ollama", lambda batch: [
        {"id": 99999, "score": 10}, {"id": batch[0]["id"], "score": "NaN"}
    ])
    with pytest.raises(RuntimeError, match="usable scores"):
        ai.select_ai_highlights(_segments(), 60, 30, 5)


def test_ai_no_silent_heuristic_fallback(monkeypatch):
    monkeypatch.delenv("CLIPFORGE_OLLAMA_URL", raising=False)
    with pytest.raises(RuntimeError, match="Ollama"):
        ai._ask_ollama([{"id": 1, "text": "test"}])
    with pytest.raises(RuntimeError, match="No speech"):
        ai.select_ai_highlights([], 90, 30, 5)


def test_captions_escape_ass_injection_and_preserve_times(tmp_path):
    segments = _segments()
    segments[0]["words"][1]["text"] = r"{\\pos(0,0)}world!"
    dest = tmp_path / "captions.ass"
    assert write_captions(segments, 0, 5, "bold", dest)
    content = dest.read_text()
    assert "{\\pos" not in content
    assert "Dialogue: 0,0:00:00.50,0:00:01.30" in content
    assert not write_captions(segments, 50, 5, "classic", dest)


def test_real_ffmpeg_renders_crop_effect_and_captions(tmp_path):
    source, dest = tmp_path / "source.mp4", tmp_path / "rendered.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
        "-i", "testsrc2=size=160x90:rate=12:duration=3", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=3", "-shortest", "-c:v", "libx264",
        "-preset", "ultrafast", "-c:a", "aac", str(source),
    ], check=True, capture_output=True)
    ass = tmp_path / "captions.ass"
    write_captions(_segments(), 0, 2, "neon", ass)
    render_clip(source, dest, 0, 2, "9:16", captions=ass, effect="pop", framing="crop")
    assert dest.stat().st_size > 1000
    assert probe(dest)["duration"] >= 1.5


def test_editing_option_validation():
    from pydantic import ValidationError

    from clipforge.app import Options

    with pytest.raises(ValidationError):
        Options(selection_mode="fake", caption_style="bold")
    with pytest.raises(ValidationError):
        Options(effect="unknown", framing="crop")
    assert Options(selection_mode="ai", caption_style="classic", effect="cinema").effect == "cinema"
