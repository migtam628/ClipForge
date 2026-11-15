import subprocess

import pytest

from clipforge.engine import probe, render_clip, render_sequence, select_highlights, validate_url


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


def test_timeline_sequence_reorders_frame_accurate_source_ranges(tmp_path):
    source = tmp_path / "two-colors.mp4"
    sequence = tmp_path / "reordered.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=red:size=160x90:rate=12:duration=2",
        "-f", "lavfi", "-i", "color=blue:size=160x90:rate=12:duration=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "libx264", str(source),
    ], check=True, capture_output=True)
    render_sequence(source, sequence, [
        {"start": 2, "duration": 1}, {"start": 0, "duration": 1},
    ], has_source_audio=False)
    assert 1.8 < probe(sequence)["duration"] < 2.2

    def pixel(at):
        result = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(at),
            "-i", str(sequence), "-frames:v", "1", "-vf", "scale=1:1,format=rgb24",
            "-f", "rawvideo", "pipe:1",
        ], check=True, capture_output=True)
        return result.stdout[:3]

    first, second = pixel(0.4), pixel(1.4)
    assert first[2] > first[0] + 60  # Blue segment moved first.
    assert second[0] > second[2] + 60  # Red segment moved second.


def test_timeline_sequence_with_source_audio_and_mixed_music(tmp_path):
    source = tmp_path / "source.mp4"
    music = tmp_path / "music.wav"
    narrator = tmp_path / "narrator.wav"
    sequence = tmp_path / "sequence.mp4"
    rendered = tmp_path / "mixed.mp4"
    narrated = tmp_path / "narrated.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=4",
        "-shortest", "-c:v", "libx264", "-c:a", "aac", str(source),
    ], check=True, capture_output=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=0.8", str(music),
    ], check=True, capture_output=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=3", str(narrator),
    ], check=True, capture_output=True)
    render_sequence(source, sequence, [
        {"start": 2, "duration": 1.5}, {"start": 0, "duration": 1.5},
    ], has_source_audio=True)
    assert probe(sequence)["has_audio"]
    render_clip(sequence, rendered, 0, 3, "9:16", has_source_audio=True,
                music=music, music_volume=0.25, source_volume=0.6)
    info = probe(rendered)
    assert info["has_audio"]
    assert 2.8 < info["duration"] < 3.2
    render_clip(sequence, narrated, 0, 3, "9:16", has_source_audio=True,
                voiceover=narrator, music=music, music_volume=0.2,
                source_volume=0.1, voice_volume=1.0)
    assert probe(narrated)["has_audio"]


def test_music_creates_audio_track_for_silent_source(tmp_path):
    source = tmp_path / "silent.mp4"
    music = tmp_path / "music.wav"
    output = tmp_path / "with-music.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=red:size=160x90:rate=10:duration=2",
        "-c:v", "libx264", str(source),
    ], check=True, capture_output=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=550:duration=0.6", str(music),
    ], check=True, capture_output=True)
    render_clip(source, output, 0, 2, "9:16", has_source_audio=False,
                music=music, music_volume=0.15)
    assert probe(output)["has_audio"]


def test_timeline_rejects_invalid_segments(tmp_path):
    with pytest.raises(ValueError, match="Invalid timeline segment"):
        render_sequence(tmp_path / "in.mp4", tmp_path / "out.mp4", [
            {"start": 0, "duration": float("nan")},
        ], has_source_audio=False)


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


def test_split_crop_contain_keeps_widescreen_shape_without_stretching(tmp_path):
    source = tmp_path / "red.mp4"
    output = tmp_path / "stack.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=red:size=320x180:rate=8:duration=1",
        "-c:v", "libx264", str(source),
    ], capture_output=True, check=True)
    # First box is a full 16:9 source; second is a narrow square. Each should
    # be independently composed into a vertical half without stretching.
    render_clip(source, output, 0, 0.75, "9:16", framing="split",
                split_fill="contain", split_boxes=[
                    {"x": 0, "y": 0, "width": 1, "height": 1},
                    {"x": 0.25, "y": 0, "width": 0.5, "height": 0.9},
                ])
    dimensions = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(output),
    ], capture_output=True, text=True, check=True)
    assert dimensions.stdout.strip() == "720,1280"
    # With contained 16:9 content, the top-left of the first pane is a bar;
    # the middle contains the original red image.
    def color_at(y):
        return subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output),
            "-vf", f"crop=2:2:360:{y},format=rgb24", "-frames:v", "1",
            "-f", "rawvideo", "pipe:1",
        ], capture_output=True, check=True).stdout[:3]

    edge, middle = color_at(20), color_at(320)
    assert middle[0] > middle[1] + 100
    assert edge[0] < 75 and edge[1] < 80 and edge[2] < 100


@pytest.mark.parametrize("ratio", ["4:5", "3:2", "21:9"])
def test_additional_ratios_render_expected_canvas(tmp_path, ratio):
    from clipforge.formats import OUTPUT_SIZES

    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=blue:size=160x90:rate=8:duration=1",
        "-c:v", "libx264", str(source),
    ], capture_output=True, check=True)
    render_clip(source, output, 0, 0.5, ratio, has_source_audio=False)
    dimensions = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(output),
    ], capture_output=True, text=True, check=True)
    assert tuple(map(int, dimensions.stdout.strip().split(","))) == OUTPUT_SIZES[ratio]


def test_split_ratio_changes_rendered_panel_height(tmp_path):
    source = tmp_path / "reaction.mp4"
    output = tmp_path / "vertical.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=red:size=160x180:rate=8:duration=1",
        "-f", "lavfi", "-i", "color=blue:size=160x180:rate=8:duration=1",
        "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]",
        "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True, capture_output=True)
    boxes = [{"x": 0, "y": 0, "width": 0.5, "height": 1},
             {"x": 0.5, "y": 0, "width": 0.5, "height": 1}]
    render_clip(source, output, 0, 0.75, "9:16", framing="split",
                split_boxes=boxes, split_ratio=0.65)
    samples = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output),
        "-vf", "select=eq(n\\,0),scale=1:20:flags=neighbor,format=rgb24",
        "-frames:v", "1", "-f", "rawvideo", "pipe:1",
    ], check=True, capture_output=True).stdout
    assert samples[12 * 3] > samples[12 * 3 + 2] + 100  # 60%: upper red
    assert samples[14 * 3 + 2] > samples[14 * 3] + 100  # 70%: lower blue


def test_split_ratio_rejects_invalid_values(tmp_path):
    for value in (0.1, 0.8, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="Split-screen panel ratio"):
            render_clip(tmp_path / "missing.mp4", tmp_path / "missing-output.mp4",
                        0, 1, "9:16", framing="split", split_ratio=value)


def test_split_render_rejects_invalid_coordinates(tmp_path):
    with pytest.raises(ValueError, match="Invalid split-screen crop box"):
        render_clip(tmp_path / "unused.mp4", tmp_path / "unused-output.mp4",
                    0, 2, "9:16", framing="split", split_boxes=[
                        {"x": 0.9, "y": 0, "width": 0.2, "height": 1},
                        {"x": 0.2, "y": 0, "width": 0.5, "height": 1},
                    ])
