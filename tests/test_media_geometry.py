"""Playback must display the decoded aspect ratio without stretching video."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipforge.app import app

STATIC = Path(__file__).resolve().parents[1] / "clipforge" / "static"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_playback_geometry_uses_decoded_dimensions():
    script = r"""
const assert = require('node:assert/strict');
const media = require(process.argv[1]);
for (const [w, h, label, orientation] of [
  [640, 480, '4:3', 'landscape'],
  [1920, 1080, '16:9', 'landscape'],
  [1080, 1920, '9:16', 'portrait'],
  [1080, 1350, '4:5', 'portrait'],
  [1080, 1080, '1:1', 'square'],
  [2520, 1080, '21:9', 'landscape']
]) {
  const shape = media.dimensions(w, h);
  assert.equal(shape.label, label);
  assert.equal(shape.orientation, orientation);
  assert.equal(shape.ratio, w / h);
}
assert.equal(media.dimensions(0, 1080), null);
assert.equal(media.dimensions(NaN, 1080), null);
assert.equal(media.dimensions(1080, -10), null);
assert.equal(media.ratioValue('4:3'), 4 / 3);
assert.equal(media.ratioValue('bad'), null);
const properties = {};
const stage = {style: {setProperty: (k, v) => properties[k] = v}, dataset: {}};
const label = {textContent: ''};
const listeners = {};
const video = {
  videoWidth: 0, videoHeight: 0, readyState: 0, dataset: {},
  addEventListener: (event, fn) => listeners[event] = fn,
};
media.observe(video, {stage, label, preset: '9:16'});
assert.equal(properties['--media-aspect'], String(9 / 16));
video.videoWidth = 1920;
video.videoHeight = 1080;
listeners.loadedmetadata();
assert.equal(properties['--media-aspect'], String(16 / 9));
assert.equal(video.dataset.playbackRatio, '16:9');
assert.equal(stage.dataset.mediaOrientation, 'landscape');
assert.equal(label.textContent, '16:9 · 1920 × 1080');
process.stdout.write(JSON.stringify({passed: true}));
"""
    result = subprocess.run(
        ["node", "-e", script, str(STATIC / "media-geometry.js")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"passed": True}


def test_every_page_loads_media_geometry_and_workspace_styles():
    html = (STATIC / "index.html").read_text()
    app = (STATIC / "app.js").read_text()
    css = (STATIC / "workspace-refresh.css").read_text()
    assert '/assets/media-geometry.js' in html
    assert '/assets/workspace-refresh.css' in html
    assert "ClipForgeMedia.observe(video" in app
    assert 'Actual playback' in app
    assert 'preview-aspect-label' in app
    assert "ClipForgeMedia.applyPreset($('portrait-preview'), outputRatio)" in app
    assert '.framing-stage video { object-fit: contain; }' in css
    assert 'aspect-ratio: var(--media-aspect, 9 / 16)' in css
    for page in ("studio", "publishing", "posting-settings", "activity", "analytics", "settings"):
        assert f"#view-{page}" in css


@pytest.mark.parametrize(
    "route", ("studio", "library", "publishing", "posting-settings", "activity", "analytics", "settings")
)
def test_workspace_page_routes_serve_playback_and_theme_assets(route):
    client = TestClient(app)
    response = client.get(f"/{route}")
    assert response.status_code == 200
    assert 'href="/assets/workspace-refresh.css"' in response.text
    assert 'src="/assets/media-geometry.js"' in response.text
    assert client.get("/assets/workspace-refresh.css").status_code == 200
    assert client.get("/assets/media-geometry.js").status_code == 200
