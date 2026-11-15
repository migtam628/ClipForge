"""Public URL previews must not weaken the import or framing flow."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from clipforge.app import app


def test_preview_is_explicit_and_the_existing_upload_monitor_remains():
    client = TestClient(app)
    html = client.get('/studio').text
    js = client.get('/assets/app.js').text
    helper = client.get('/assets/preview-url.js')

    assert helper.status_code == 200
    assert html.index('/assets/preview-url.js') < html.index('/assets/app.js')
    assert 'id="load-url-preview"' in html
    assert 'id="framing-embed"' in html
    assert 'id="framing-source"' in html
    assert 'id="portrait-image-a"' in html
    assert 'id="portrait-image-b"' in html
    assert 'referrerpolicy="strict-origin-when-cross-origin"' in html
    assert 'id="framing-embed"' in html.split('src="/assets/app.js"')[0]
    assert 'load-url-preview' in js
    assert 'previewYoutube.sourceUrl === selectedUrlForPreview()' in js
    assert 'URL.createObjectURL(files[0])' in js
    assert 'YouTube thumbnail crop guide only' in js


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is not installed')
def test_youtube_preview_url_parser_uses_only_safe_video_ids():
    source = Path(__file__).resolve().parents[1] / 'clipforge/static/preview-url.js'
    cases = {
        'https://www.youtube.com/watch?v=83K8wFepWTE': '83K8wFepWTE',
        'https://m.youtube.com/watch?v=83K8wFepWTE&t=5': '83K8wFepWTE',
        'https://youtu.be/83K8wFepWTE?t=15': '83K8wFepWTE',
        'https://www.youtube.com/shorts/83K8wFepWTE': '83K8wFepWTE',
        'https://www.youtube.com/live/83K8wFepWTE': '83K8wFepWTE',
        'https://www.youtube.com/embed/83K8wFepWTE': '83K8wFepWTE',
        'https://youtube.com.evil.test/watch?v=83K8wFepWTE': None,
        'https://evil.test/?v=83K8wFepWTE': None,
        'javascript:alert(1)': None,
        'http://youtu.be/83K8wFepWTE': None,
        'https://user@youtube.com/watch?v=83K8wFepWTE': None,
        'https://www.youtube.com/watch?v=1': None,
        'https://youtu.be/83K8wFepWTE/extra': None,
        'https://www.youtube.com/watch?v=83K8wFepWTE%3Cscript%3E': None,
        'https://vimeo.com/12345': None,
        'not a url': None,
    }
    script = '''
const {parseYouTubePreview} = require(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
for (const [url, expected] of Object.entries(cases)) {
  const preview = parseYouTubePreview(url);
  if (expected === null) {
    if (preview !== null) throw Error('Unsafe preview accepted: ' + url);
  } else if (!preview || preview.id !== expected ||
    preview.embedUrl !== `https://www.youtube-nocookie.com/embed/${expected}?rel=0` ||
    preview.thumbnailUrl !== `https://i.ytimg.com/vi/${expected}/hqdefault.jpg`) {
    throw Error('Unexpected preview conversion: ' + url);
  }
}
'''
    subprocess.run(['node', '-e', script, str(source), json.dumps(cases)], check=True)
