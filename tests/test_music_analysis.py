"""Bounded rhythmic-accent inspection for uploaded editor soundtracks."""
from __future__ import annotations

import math
import struct
import wave

from fastapi.testclient import TestClient

from clipforge import app as module
from clipforge.music_analysis import inspect_music_rhythm, rhythmic_accents

client = TestClient(module.app)


def _pulse_track(path):
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        samples = []
        for index in range(8000 * 4):
            second = index / 8000
            loud = any(beat <= second < beat + 0.17 for beat in (0.5, 1.3, 2.1, 2.9))
            value = int(16000 * math.sin(2 * math.pi * 440 * second)) if loud else 0
            samples.append(struct.pack("<h", value))
        writer.writeframes(b"".join(samples))


def test_music_accents_detect_actual_transients_and_reuse_cached_result(tmp_path):
    track = tmp_path / "licensed.wav"
    _pulse_track(track)
    first = inspect_music_rhythm(track, tmp_path / "rhythm.json")
    assert first["method"] == "energy_transients"
    assert first["review_required"] is True
    assert not first["cached"]
    assert len(first["markers"]) >= 3
    assert any(0.4 <= t <= 0.7 for t in first["markers"])
    second = inspect_music_rhythm(track, tmp_path / "rhythm.json")
    assert second["cached"]
    assert second["markers"] == first["markers"]
    assert len(rhythmic_accents(track)["markers"]) <= 24


def test_rhythm_endpoint_reads_only_selected_music_and_never_other_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "sample")
    module._patch(folder, status="completed", duration=10)
    endpoint = f"/api/jobs/{job_id}/music/rhythm"
    assert client.get(endpoint).status_code == 404
    (folder / "music").mkdir()
    track = folder / "music" / ("track-" + "a" * 32 + ".wav")
    _pulse_track(track)
    module._patch(folder, music_file=track.name)
    response = client.get(endpoint)
    assert response.status_code == 200, response.text
    assert response.json()["review_required"] is True
    module._patch(folder, music_file="../source.mp4")
    assert client.get(endpoint).status_code == 404
