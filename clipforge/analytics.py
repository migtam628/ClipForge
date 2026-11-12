"""Creator metrics with explicit provenance, snapshot semantics and safe CSV export.

Manual snapshots are self-reported; do not invent provider data, estimates or
revenue. The latest snapshot for a post supersedes earlier snapshots.
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

METRICS = ("views", "likes", "comments", "shares", "saves")
PLATFORMS = (
    "youtube", "instagram", "facebook", "tiktok", "x", "threads", "snapchat", "other",
)


def post_key(platform: str, post_url: str, job_id: str, filename: str) -> str:
    """Stable post identity, distinct per platform, not per snapshot."""
    if post_url:
        return platform + ":url:" + post_url.strip()
    return platform + ":clip:" + job_id + ":" + filename


def valid_https_url(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and bool(parsed.hostname) and
                parsed.username is None and parsed.password is None and
                parsed.port in (None, 443))
    except (ValueError, TypeError):
        return False


def latest_records(rows: list[dict]) -> list[dict]:
    # Records are appended atomically. Do not sum multiple snapshots from
    # the same post, which would inflate views and payouts.
    grouped = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("post_key"), str):
            grouped[row["post_key"]] = row
    return sorted(grouped.values(), key=lambda row: row.get("captured_at", ""),
                  reverse=True)


def _money(value: str | int | Decimal) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def summarize(records: list[dict], publication_counts: dict[str, int] | None = None) -> dict:
    latest = latest_records(records)
    totals = {metric: 0 for metric in METRICS}
    revenue = Decimal("0")
    by_platform = {}
    campaigns = {}
    for record in latest:
        name = record["platform"]
        group = by_platform.setdefault(name, {
            "platform": name, "posts": 0, **{key: 0 for key in METRICS},
            "earnings_usd": "0.00",
        })
        group["posts"] += 1
        for metric in METRICS:
            value = max(0, int(record.get(metric, 0)))
            totals[metric] += value
            group[metric] += value
        amount = _money(record.get("earnings_usd", "0.00"))
        revenue += amount
        group["earnings_usd"] = str(_money(group["earnings_usd"]) + amount)
        status = record.get("campaign_status", "none")
        if status != "none":
            campaigns[status] = campaigns.get(status, 0) + 1
    return {
        "provenance": "self_reported_manual_snapshots",
        "verified_platform_metrics": False,
        "tracked_posts": len(latest),
        **totals,
        "engagements": totals["likes"] + totals["comments"] +
                       totals["shares"] + totals["saves"],
        "earnings_usd": str(revenue),
        "by_platform": sorted(by_platform.values(), key=lambda x: -x["views"]),
        "campaign_status_counts": campaigns,
        "publication_states": publication_counts or {},
        "latest": latest[:100],
        "last_recorded_at": latest[0]["captured_at"] if latest else None,
    }


def safe_csv(records: list[dict]) -> str:
    """CSV injection guard for names, links and notes opened in spreadsheets."""
    fields = [
        "id", "captured_at", "source", "platform", "post_url",
        "job_id", "filename", "campaign_name", "campaign_status",
        *METRICS, "earnings_usd", "evidence_url", "notes",
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(fields)
    for row in records:
        cells = []
        for field in fields:
            value = str(row.get(field, ""))
            if value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value.lstrip()
            cells.append(value)
        writer.writerow(cells)
    return output.getvalue()
