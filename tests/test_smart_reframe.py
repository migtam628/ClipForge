"""CPU-friendly social video reframing and per-word animation tests."""
import subprocess

import pytest

from clipforge.captions import write_captions, write_narration_captions
from clipforge.engine import probe, render_clip
from clipforge.smart_frame import crop_positions, write_tracking_commands


def test_smart_positions_are_bounded_and_smoothed():
    points = [(float(i), 0.9 if i % 2 else 0.1) for i in range(12)]
    positions = crop_positions(points, 1920, 1080, 720, 1280)
    assert len(positions) == len(points)
    assert all(t >= 0 and 0 <= x <= 1556 for t, x in positions)
    assert len({x for _, x in positions}) > 1
    assert max(abs(a[1] - b[1]) for a, b in zip(positions, positions[1:])) < 850
    assert crop_positions(points, 0, 100) == []


def test_tracking_command_file_never_includes_user_text(tmp_path):
    output = tmp_path / "track.txt"
    assert write_tracking_commands([(0.0, 12), (1.5, 230)], output)
    assert output.read_text().splitlines() == [
        "0.00 crop@smart x 12;",
        "1.50 crop@smart x 230;",
    ]
    assert not write_tracking_commands([], output)


def test_karaoke_and_pop_captions_use_word_timestamps(tmp_path):
    transcript = [{"words": [
        {"start": 1.0, "end": 1.4, "text": "Listen"},
        {"start": 1.4, "end": 1.9, "text": "up"},
        {"start": 2.2, "end": 2.6, "text": "NOW!"},
        {"start": 3.2, "end": 3.5, "text": "{unsafe}\\N"},
    ]}]
    karaoke = tmp_path / "karaoke.ass"
    pop = tmp_path / "pop.ass"
    assert write_captions(transcript, 1, 4, "karaoke", karaoke)
    assert write_captions(transcript, 1, 4, "pop", pop)
    assert r"{\kf" in karaoke.read_text()
    assert r"{\t(0,140," in pop.read_text()
    assert "{unsafe}" not in pop.read_text()
    assert r"\N}" not in karaoke.read_text()
    assert write_narration_captions("This changes everything", 2, 4, "karaoke", tmp_path / "voice.ass")


def test_real_ffmpeg_smart_crop_and_blurred_fallback(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=8:duration=1.2",
        "-c:v", "libx264", "-preset", "ultrafast", str(source),
    ], check=True, capture_output=True)
    commands = tmp_path / "tracking.txt"
    assert write_tracking_commands([(0, 22), (0.5, 60)], commands)
    for framing, track in (("smart", commands), ("blur", None)):
        dest = tmp_path / (framing + ".mp4")
        render_clip(
            source, dest, 0, 1, "9:16", effect="none",
            framing=framing, smart_track=track, has_source_audio=False,
        )
        assert dest.stat().st_size > 500
        assert 0.8 <= probe(dest)["duration"] <= 1.2


def test_smart_mode_without_face_track_centers_instead_of_failing(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=8:duration=1",
        "-c:v", "libx264", "-preset", "ultrafast", str(source),
    ], check=True, capture_output=True)
    dest = tmp_path / "fallback.mp4"
    render_clip(source, dest, 0, 0.75, "9:16", framing="smart")
    assert dest.is_file()


def test_new_framing_options_validate_through_api():
    from clipforge.app import Options

    assert Options(framing="smart", caption_style="karaoke").framing == "smart"
    assert Options(framing="blur", caption_style="pop").caption_style == "pop"
    with pytest.raises(ValueError):
        Options(framing="teleport")
