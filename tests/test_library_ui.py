"""Library layout and navigation regression coverage."""
import re

from fastapi.testclient import TestClient

from clipforge.app import app

client = TestClient(app)


def test_library_page_has_three_views_and_status_filter_controls():
    response = client.get("/library")
    assert response.status_code == 200
    html = response.text
    for element_id in (
        "library-total-clips", "library-total-sources", "library-search",
        "library-sort", "library-summary", "jobs-library", "library-status-tabs",
    ):
        assert f'id="{element_id}"' in html
    for view in ("grid", "list", "review"):
        assert f'data-library-view="{view}"' in html
    for status in ("all", "ready", "processing", "failed"):
        assert f'data-library-status="{status}"' in html
    assert 'href="/studio"' in html


def test_library_frontend_can_reuse_existing_job_api():
    javascript = client.get("/assets/app.js")
    stylesheet = client.get("/assets/style.css")
    assert javascript.status_code == 200
    assert stylesheet.status_code == 200
    source = javascript.text
    css = stylesheet.text
    assert "function renderLibrary()" in source
    assert "libraryJobs = shown;" in source
    for function_name in ("renderLibraryGrid", "renderLibraryList", "renderLibraryReview"):
        assert f"function {function_name}(" in source
    assert "buildClipEditor(selected.job, selected.clip)" in source
    assert "libraryStatusLabel(entry)" in source
    assert "clipforge.library.view" in source
    assert "function buildLibraryAiAssistant(job, clip)" in source
    assert "buildLibraryAiAssistant(selected.job, selected.clip)" in source
    assert "buildLibraryAiAssistant(entry.job, entry.clip)" in source
    assert "?regenerate=true" in source
    assert "Nothing publishes automatically" in source
    assert "fetch('/api/jobs', {cache: 'no-store'})" in source
    for selector in (".media-library-grid", ".media-library-table", ".media-library-review", ".media-library-ai"):
        assert selector in css


def test_script_startup_references_existing_dom_ids():
    """All static DOM lookups must resolve before refresh populates Library."""
    html = client.get("/library").text
    javascript = client.get("/assets/app.js").text
    referenced = set(re.findall(r"\$\(['\"]([^'\"]+)['\"]\)", javascript))
    # Notifications create this node on demand, so it is not part of the HTML.
    referenced.discard("global-toast")
    missing = sorted(element_id for element_id in referenced
                     if f'id="{element_id}"' not in html)
    assert missing == []


def test_library_supports_responsive_clips_and_progress_percent():
    source = client.get("/assets/app.js").text
    css = client.get("/assets/style.css").text
    assert "libraryProgress(entry.job)" in source
    assert "bar.setAttribute('aria-valuenow', String(percent))" in source
    assert "video.preload = 'metadata'" in source
    assert "@media (max-width: 750px)" in css
    assert ".media-library-review-rail" in css


def test_workspace_navigation_remains_available_in_responsive_styles():
    html = client.get("/studio").text
    assert 'data-page="library"' in html
    assert 'data-page="publishing"' in html
    assert 'data-page="activity"' in html
    assert 'data-page="settings"' in html
    assert ".sidebar" in client.get("/assets/style.css").text


def test_active_job_progress_is_shown_as_a_percentage():
    javascript = client.get("/assets/app.js").text
    stylesheet = client.get("/assets/style.css").text
    assert "Math.min(100, Math.max(0, Math.round(Number(job.progress))))" in javascript
    assert "'job-progress-value', `${progress}%`" in javascript
    assert "bar.setAttribute('role', 'progressbar')" in javascript
    assert "bar.setAttribute('aria-valuenow', String(progress))" in javascript
    assert "fill.style.width = `${progress}%`" in javascript
    assert ".job-progress-value" in stylesheet
