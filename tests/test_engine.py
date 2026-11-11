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
