import subprocess

import pytest

from clipforge.engine import probe, render_clip, select_highlights, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://www.twitch.tv/videos/123",
        "https://kick.com/channel/videos/456",
        "https://vimeo.com/123",
    ],
)
def test_supported_sources(url):
    assert validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=1",
        "https://youtube.com.evil.test/watch?v=1",
        "file:///etc/passwd",
        "https://127.0.0.1/video",
        "https://youtube.com:8080/watch?v=1",
        "https://user:pass@youtube.com/watch?v=1",
        "https://example.org/file.mp4",
        "https://[::1]/video",
    ],
)
def test_url_denials(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_highlights_are_bounded_and_non_overlapping():
    scores = [100 if 15 <= x <= 35 else 1 for x in range(90)]
    clips = select_highlights(180, 30, 4, scores)
    assert 1 <= len(clips) <= 4
    assert clips == sorted(clips, key=lambda c: c["start"])
    assert all(clips[i + 1]["start"] >= clips[i]["start"] + 30 for i in range(len(clips) - 1))
    assert any(20 <= clip["start"] <= 70 for clip in clips)


def test_silent_video_uses_uniform_fallback():
    clips = select_highlights(65, 30, 25, [])
    assert len(clips) == 2
    assert clips[0]["start"] == 0
    assert clips[1]["start"] >= 30


def test_short_video_returns_no_clips():
    assert select_highlights(2.9, 30, 25) == []


def test_ffmpeg_render_and_probe(tmp_path):
    source = tmp_path / "source.mp4"
    dest = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            "testsrc2=size=320x180:rate=12:duration=4", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=8000:duration=4", "-shortest", "-c:v", "libx264",
            "-preset", "ultrafast", "-c:a", "aac", str(source),
        ],
        check=True,
        capture_output=True,
    )
    assert probe(source)["duration"] >= 3.5
    render_clip(source, dest, 1, 2, "9:16")
    assert dest.stat().st_size > 500
    result = probe(dest)
    assert 1.5 <= result["duration"] <= 2.5


def test_split_screen_renders_two_source_regions(tmp_path):
    """Stacked reactions contain the chosen left/right source regions, not duplicates."""
    source = tmp_path / "reaction.mp4"
    output = tmp_path / "vertical.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=red:size=160x180:rate=8:duration=2",
        "-f", "lavfi", "-i", "color=blue:size=160x180:rate=8:duration=2",
        "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]",
        "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True, capture_output=True)
    render_clip(source, output, 0, 1.5, "9:16", framing="split", split_boxes=[
        {"x": 0, "y": 0, "width": 0.5, "height": 1},
        {"x": 0.5, "y": 0, "width": 0.5, "height": 1},
    ])
    assert output.stat().st_size > 500
    dimensions = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(output),
    ], capture_output=True, text=True, check=True)
    assert dimensions.stdout.strip() == "720,1280"
    sample = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output),
        "-vf", "select=eq(n\\,0),scale=1:2:flags=area,format=rgb24",
        "-frames:v", "1", "-f", "rawvideo", "pipe:1",
    ], capture_output=True, check=True)
    top, bottom = sample.stdout[:3], sample.stdout[3:6]
    assert top[0] > top[2] + 100
    assert bottom[2] > bottom[0] + 100


def test_split_render_rejects_invalid_coordinates(tmp_path):
    with pytest.raises(ValueError, match="Invalid split-screen crop box"):
        render_clip(tmp_path / "unused.mp4", tmp_path / "unused-output.mp4",
                    0, 2, "9:16", framing="split", split_boxes=[
                        {"x": 0.9, "y": 0, "width": 0.2, "height": 1},
                        {"x": 0.2, "y": 0, "width": 0.5, "height": 1},
                    ])
