"""CPU-friendly social video reframing and per-word animation tests."""
import subprocess
import sys
from types import SimpleNamespace

import pytest

from clipforge.captions import write_captions, write_narration_captions
from clipforge.engine import probe, render_clip
from clipforge.smart_frame import (
    crop_follow_path,
    crop_positions,
    plan_face_tracking,
    write_tracking_commands,
)


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


def test_subject_follow_moves_both_axes_without_jumps():
    # Wide input to tall output: camera should pan horizontally, not vertically.
    horizontal = crop_follow_path(
        [(0, .1, .2), (1, .85, .8), (2, .95, .9)],
        1920, 1080, 720, 1280,
    )
    assert len(horizontal) >= 40
    assert horizontal[0][0] == 0
    assert len({x for _, x, _ in horizontal}) > 1
    assert {y for _, _, y in horizontal} == {0}
    assert max(abs(a[1] - b[1]) for a, b in zip(horizontal, horizontal[1:])) < 250
    assert max(b[0] - a[0] for a, b in zip(horizontal, horizontal[1:])) <= 0.06

    # Tall input to wide output: camera should pan vertically, not horizontally.
    vertical = crop_follow_path(
        [(0, .5, .1), (1, .5, .9), (2, .5, .95)],
        1080, 1920, 1280, 720,
    )
    assert {x for _, x, _ in vertical} == {0}
    assert len({y for _, _, y in vertical}) > 1
    assert max(abs(a[2] - b[2]) for a, b in zip(vertical, vertical[1:])) < 250


def test_subject_follow_filters_jitter_and_invalid_geometry():
    points = [(0, .5, .5), (.5, .5001, .5002), (1, .4999, .4998)]
    result = crop_follow_path(points, 1920, 1080, 720, 1280)
    assert len({x for _, x, _ in result}) == 1
    assert crop_follow_path(points, 0, 1080) == []
    assert crop_follow_path([(float('nan'), .5, .5)], 1920, 1080) == []
    with pytest.raises(ValueError):
        crop_follow_path(points, 1920, 1080, step=0.01)
    with pytest.raises(ValueError):
        crop_follow_path([(0, .5, .5), (121, .5, .5)], 1920, 1080)


def test_tracking_commands_support_vertical_movement(tmp_path):
    commands = tmp_path / "xy-track.txt"
    assert write_tracking_commands([(0, 15, 30), (.2, 20, 45)], commands)
    assert commands.read_text().splitlines() == [
        "0.00 crop@smart x 15, crop@smart y 30;",
        "0.20 crop@smart x 20, crop@smart y 45;",
    ]


def test_face_detector_plan_emits_vertical_follow_commands(tmp_path, monkeypatch):
    class FakeCapture:
        index = -1
        released = False

        def isOpened(self):
            return True

        def get(self, property_id):
            return {3: 180, 4: 320}[property_id]

        def set(self, *_):
            pass

        def read(self):
            self.index += 1
            return True, SimpleNamespace(shape=(320, 180, 3))

        def release(self):
            self.released = True

    capture = FakeCapture()

    class FakeCascade:
        def empty(self):
            return False

        def detectMultiScale(self, *_args, **_kwargs):
            return [(55, [20, 190, 240][capture.index], 40, 40)]

    fake_cv2 = SimpleNamespace(
        VideoCapture=lambda _: capture,
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        CAP_PROP_POS_MSEC=5,
        COLOR_BGR2GRAY=6,
        CascadeClassifier=lambda _: FakeCascade(),
        data=SimpleNamespace(haarcascades="/fake/"),
        resize=lambda frame, *_args, **_kwargs: frame,
        cvtColor=lambda *_args: SimpleNamespace(shape=(320, 180)),
        error=RuntimeError,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    output = tmp_path / "path.txt"
    assert plan_face_tracking(tmp_path / "source.mp4", 0, 1.5, output, 1280, 720)
    commands = output.read_text().splitlines()
    assert len(commands) >= 6
    assert all("crop@smart x" in line and "crop@smart y" in line for line in commands)
    assert len({line.split("crop@smart y ")[1].rstrip(";") for line in commands}) > 1
    assert capture.released


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
    assert r"\t(0,140," in pop.read_text()
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


def test_real_ffmpeg_two_axis_tracking_file_is_accepted(tmp_path):
    source = tmp_path / "portrait.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=red:s=180x160:r=10:d=1",
        "-f", "lavfi", "-i", "color=c=blue:s=180x160:r=10:d=1",
        "-filter_complex", "[0:v][1:v]vstack=inputs=2[out]", "-map", "[out]",
        "-c:v", "libx264", "-preset", "ultrafast", str(source),
    ], check=True, capture_output=True)
    path = tmp_path / "follow.txt"
    assert write_tracking_commands([(0.0, 0, 0), (0.5, 0, 1530)], path)
    output = tmp_path / "landscape.mp4"
    render_clip(source, output, 0, .95, "16:9", framing="smart",
                smart_track=path, has_source_audio=False)
    assert output.stat().st_size > 500

    def center_pixel(timestamp):
        result = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output),
            "-ss", str(timestamp), "-frames:v", "1", "-vf", "scale=1:1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], capture_output=True, check=True)
        return result.stdout[:3]

    before, after = center_pixel(.15), center_pixel(.75)
    assert before[0] > before[2] * 2, f"Expected red top frame, got {before!r}"
    assert after[2] > after[0] * 2, f"Expected blue bottom frame, got {after!r}"


def test_new_framing_options_validate_through_api():
    from clipforge.app import Options

    assert Options(framing="smart", caption_style="karaoke").framing == "smart"
    assert Options(framing="blur", caption_style="pop").caption_style == "pop"
    with pytest.raises(ValueError):
        Options(framing="teleport")
