"""Real FFmpeg regression tests for accurate video shape and non-stretched exports."""

import json
import shutil
import subprocess

import pytest

from clipforge import quality
from clipforge.engine import render_clip
from clipforge.formats import OUTPUT_SIZES

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and FFprobe required",
)


@pytest.fixture(scope="module", params=[(320, 240), (240, 320)])
def source_video(tmp_path_factory, request):
    """Test centered-square markers from both landscape and portrait sources."""
    width, height = request.param
    path = tmp_path_factory.mktemp("geometry") / "square-marker.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i",
        f"color=c=black:s={width}x{height}:r=8:d=1.0,"
        f"drawbox=x={(width - 80) // 2}:y={(height - 80) // 2}:"
        "w=80:h=80:color=white:t=fill",
        "-c:v", "libx264", "-preset", "ultrafast", str(path),
    ], check=True, capture_output=True)
    return path


def frame_bounds(path, width, height):
    """Find the bright marker after decoding; compare visual X/Y dimensions."""
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "0.25",
        "-i", str(path), "-frames:v", "1", "-pix_fmt", "gray",
        "-f", "rawvideo", "pipe:1",
    ], capture_output=True, check=True)
    raw = result.stdout
    assert len(raw) == width * height
    pixels = [i for i, value in enumerate(raw) if value > 220]
    assert pixels, "Expected white square marker to remain visible after reframing"
    xs = [index % width for index in pixels]
    ys = [index // width for index in pixels]
    return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


@pytest.mark.parametrize("ratio", ["9:16", "4:3", "16:9"])
@pytest.mark.parametrize("framing", ["pad", "crop", "smart", "blur"])
def test_export_preserves_square_pixels_and_marker_shape(
    source_video, tmp_path, ratio, framing,
):
    dest = tmp_path / "output.mp4"
    render_clip(
        source_video, dest, 0, 0.75, ratio,
        framing=framing, has_source_audio=False,
    )
    measured = quality._media_inspection(dest)
    width, height = OUTPUT_SIZES[ratio]
    assert (measured["width"], measured["height"]) == (width, height)
    assert measured["sample_aspect_ratio"] == "1:1"
    marker_width, marker_height = frame_bounds(dest, width, height)
    assert abs(marker_width / marker_height - 1) < .04, (
        f"{framing} {ratio} distorted source square to "
        f"{marker_width}×{marker_height}"
    )
    report = quality.inspect_rendered_clip(
        dest, expected_duration=.75, aspect_ratio=ratio,
        source_has_audio=False, narration="none", script=None, beats=[],
        captions_requested=False, captions_added=False, candidate={},
    )
    assert report["technical_pass"], json.dumps(report, indent=2)
    assert report["metrics"]["sample_aspect_ratio"] == "1:1"


def test_quality_gate_catches_stretched_pixel_aspect(monkeypatch, tmp_path):
    monkeypatch.setattr(quality, "_media_inspection", lambda _: {
        "duration": 10, "width": 960, "height": 720,
        "sample_aspect_ratio": "4:3", "has_audio": False,
    })
    report = quality.inspect_rendered_clip(
        tmp_path / "sample.mp4", expected_duration=10, aspect_ratio="4:3",
        source_has_audio=False, narration="none", script=None, beats=[],
        captions_requested=False, captions_added=False, candidate={},
    )
    assert not report["technical_pass"]
    assert report["technical_issues"] == ["render_pixel_aspect_mismatch"]


def test_all_export_presets_have_accurate_even_pixel_dimensions():
    for label, (width, height) in OUTPUT_SIZES.items():
        x, y = map(int, label.split(":"))
        assert width % 2 == height % 2 == 0
        assert width * y == height * x
