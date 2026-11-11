"""Manual creator metrics must be bounded, auditable and never fabricated."""
import csv
import io

from fastapi.testclient import TestClient

from clipforge import analytics
from clipforge import app as module

client = TestClient(module.app)


def _make_job(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    job_id, folder = module._create("upload", module.Options(), "Owned video")
    module._patch(folder, status="completed", clips=[{
        "filename": "clip-001.mp4", "number": 1, "start": 0, "duration": 20,
    }], publications=[{
        "id": "scheduled-id", "filename": "clip-001.mp4", "platform": "youtube",
        "state": "scheduled",
    }])
    return job_id, folder


def test_analytics_starts_empty_and_discloses_manual_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    response = client.get("/api/analytics/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["tracked_posts"] == 0
    assert body["views"] == 0
    assert body["earnings_usd"] == "0"
    assert body["verified_platform_metrics"] is False
    assert body["provenance"] == "self_reported_manual_snapshots"


def test_latest_snapshot_replaces_older_post_stats_in_summary(tmp_path, monkeypatch):
    job_id, _ = _make_job(tmp_path, monkeypatch)
    base = {
        "platform": "youtube", "job_id": job_id, "filename": "clip-001.mp4",
        "views": 100, "likes": 10, "earnings_usd": "3.50",
        "campaign_name": "Creator program", "campaign_status": "submitted",
    }
    first = client.post("/api/analytics/records", json=base)
    assert first.status_code == 201, first.text
    second = client.post("/api/analytics/records", json={
        **base, "views": 250, "likes": 22, "earnings_usd": "10.75",
        "campaign_status": "paid",
    })
    assert second.status_code == 201, second.text
    summary = client.get("/api/analytics/summary").json()
    assert summary["tracked_posts"] == 1
    assert summary["views"] == 250  # not 350
    assert summary["likes"] == 22   # not 32
    assert summary["earnings_usd"] == "10.75"  # not 14.25
    assert summary["campaign_status_counts"] == {"paid": 1}
    assert summary["publication_states"]["scheduled"] == 1
    records = client.get("/api/analytics/records").json()
    assert len(records) == 2
    assert records[0]["source"] == "self_reported_manual"
    exported = client.get("/api/analytics/export")
    assert exported.status_code == 200
    reader = csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff")))
    rows = list(reader)
    assert len(rows) == 2
    assert rows[-1]["earnings_usd"] == "10.75"
    deleted = client.delete("/api/analytics/records/" + second.json()["id"])
    assert deleted.status_code == 204
    assert client.get("/api/analytics/summary").json()["views"] == 100


def test_external_https_post_and_platform_aggregation(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    url = "https://www.youtube.com/shorts/test-video"
    a = client.post("/api/analytics/records", json={
        "platform": "youtube", "post_url": url, "views": 1000,
        "earnings_usd": "2.25",
    })
    b = client.post("/api/analytics/records", json={
        "platform": "tiktok", "post_url": url, "views": 5000,
        "earnings_usd": "0.00",
    })
    assert a.status_code == 201
    assert b.status_code == 201
    stats = client.get("/api/analytics/summary").json()
    assert stats["tracked_posts"] == 2
    assert stats["views"] == 6000
    assert stats["earnings_usd"] == "2.25"
    assert {p["platform"] for p in stats["by_platform"]} == {"youtube", "tiktok"}


def test_invalid_metrics_are_rejected_without_files(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    path = "/api/analytics/records"
    assert client.post(path, json={"platform": "youtube"}).status_code == 422
    assert client.post(path, json={
        "platform": "youtube", "post_url": "javascript:alert(1)",
    }).status_code == 422
    assert client.post(path, json={
        "platform": "youtube", "post_url": "https://www.youtube.com/shorts/x",
        "earnings_usd": "-10",
    }).status_code == 422
    assert client.post(path, json={
        "platform": "youtube", "post_url": "https://www.youtube.com/shorts/x",
        "views": -1,
    }).status_code == 422
    assert client.post(path, json={
        "platform": "youtube", "job_id": "1" * 32, "filename": "clip-001.mp4",
    }).status_code == 404
    assert not (tmp_path / "analytics.json").exists()


def test_export_disarms_formula_names_and_notes():
    csv_data = analytics.safe_csv([{
        "platform": "youtube", "campaign_name": "=HYPERLINK(bad)",
        "notes": " @dangerous_formula", "earnings_usd": "1.00",
    }])
    assert "'=HYPERLINK(bad)" in csv_data
    assert "'@dangerous_formula" in csv_data


def test_dashboard_contains_earnings_and_campaign_status_controls():
    page = client.get("/analytics")
    assert page.status_code == 200
    assert 'id="view-analytics"' in page.text
    assert 'id="analytics-earnings"' in page.text
    assert 'id="analytics-campaign-status"' in page.text
    assert 'data-page="analytics"' in page.text
    script = client.get("/assets/analytics.js").text
    assert "loadAnalytics" in script
    assert "saveAnalytics" in script
    assert '/assets/analytics.js' in page.text
