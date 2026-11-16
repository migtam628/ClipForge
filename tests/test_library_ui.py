"""Library layout and navigation regression coverage."""
import re

from fastapi.testclient import TestClient

from clipforge.app import app

client = TestClient(app)


def test_library_page_has_search_filter_and_sort_controls():
    response = client.get("/library")
    assert response.status_code == 200
    html = response.text
    for element_id in (
        "library-metrics", "library-total-clips", "library-total-sources",
        "library-total-narrated", "library-search", "library-filter",
        "library-sort", "library-summary", "jobs-library",
    ):
        assert f'id="{element_id}"' in html
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
    assert "libraryExpanded" in source
    assert "fetch('/api/jobs', {cache: 'no-store'})" in source
    for selector in (".library-overview", ".library-folder", ".library-clip"):
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
