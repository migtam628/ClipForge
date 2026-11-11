"""Creator-approved social posting using official, authenticated platform APIs.

Credentials come from server environment only, never the browser or job.json.
Provider calls are isolated in a worker; UI never claims a draft was published.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

SOCIALS = {
    "youtube": {"label": "YouTube Shorts", "mode": "direct", "required": (
        "CLIPFORGE_YOUTUBE_CLIENT_ID", "CLIPFORGE_YOUTUBE_CLIENT_SECRET",
        "CLIPFORGE_YOUTUBE_REFRESH_TOKEN")},
    "instagram": {"label": "Instagram Reels", "mode": "direct", "required": (
        "CLIPFORGE_INSTAGRAM_TOKEN", "CLIPFORGE_INSTAGRAM_USER_ID")},
    "facebook": {"label": "Facebook Page Reels", "mode": "direct", "required": (
        "CLIPFORGE_FACEBOOK_PAGE_ACCESS_TOKEN",)},
    "tiktok": {"label": "TikTok Inbox Draft", "mode": "draft", "required": (
        "CLIPFORGE_TIKTOK_ACCESS_TOKEN",)},
}


def platform_status() -> list[dict]:
    return [{"id": key, "name": value["label"], "mode": value["mode"],
             "configured": all(bool(os.environ.get(field)) for field in value["required"])}
            for key, value in SOCIALS.items()]


def _api_version() -> str:
    value = os.environ.get("CLIPFORGE_META_API_VERSION", "v24.0")
    if not re.fullmatch(r"v\d+\.\d+", value):
        raise ValueError("Invalid Meta Graph API version")
    return value


def _json_request(url: str, *, method: str = "POST", token: str = "",
                  payload: dict | None = None, form: dict | None = None,
                  headers: dict | None = None) -> dict:
    if form is not None:
        body = urlencode(form).encode()
        kind = "application/x-www-form-urlencoded"
    elif payload is not None:
        body = json.dumps(payload).encode()
        kind = "application/json"
    else:
        body = None
        kind = "application/json"
    all_headers = {"Content-Type": kind, **(headers or {})}
    if token:
        all_headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, method=method, headers=all_headers)
    try:
        with urlopen(request, timeout=90) as response:
            return json.loads(response.read(1_000_000))
    except HTTPError as exc:
        # Do not echo access tokens or possibly sensitive response payloads into job logs.
        raise RuntimeError(f"{urlsplit(url).hostname} rejected the request (HTTP {exc.code})") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Unable to reach {urlsplit(url).hostname}") from exc


def _upload_file(url: str, video: Path, headers: dict, *, allow: str,
                 start: int = 0, count: int | None = None) -> dict:
    parsed = urlsplit(url)
    domain = parsed.hostname or ""
    if parsed.scheme != "https" or (domain != allow and not domain.endswith("." + allow)):
        raise RuntimeError("Upload API returned an unexpected destination")
    size = video.stat().st_size
    amount = count if count is not None else size
    if amount <= 0:
        raise ValueError("Cannot upload an empty video")
    import http.client
    connection = http.client.HTTPSConnection(domain, timeout=300)
    try:
        path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        connection.putrequest("PUT" if domain.endswith("tiktokapis.com") or
                              domain.endswith("googleapis.com") else "POST", path)
        for key, value in {"Content-Length": str(amount), **headers}.items():
            connection.putheader(key, str(value))
        connection.endheaders()
        with video.open("rb") as stream:
            stream.seek(start)
            remaining = amount
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError("Video ended unexpectedly during upload")
                connection.send(chunk)
                remaining -= len(chunk)
        response = connection.getresponse()
        content = response.read(1_000_000)
        if response.status not in (200, 201, 202, 206):
            raise RuntimeError(f"{domain} rejected video upload (HTTP {response.status})")
        return json.loads(content) if content.strip() else {}
    finally:
        connection.close()


def _youtube_token() -> str:
    result = _json_request("https://oauth2.googleapis.com/token", form={
        "client_id": os.environ["CLIPFORGE_YOUTUBE_CLIENT_ID"],
        "client_secret": os.environ["CLIPFORGE_YOUTUBE_CLIENT_SECRET"],
        "refresh_token": os.environ["CLIPFORGE_YOUTUBE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    })
    if not result.get("access_token"):
        raise RuntimeError("YouTube OAuth refresh failed")
    return result["access_token"]


def _youtube(video: Path, title: str, description: str, visibility: str) -> dict:
    if visibility not in {"private", "unlisted", "public"}:
        raise ValueError("Invalid YouTube visibility")
    token = _youtube_token()
    import urllib.request

    metadata = json.dumps({
        "snippet": {"title": title[:100], "description": description[:5000],
                    "categoryId": "22"},
        "status": {"privacyStatus": visibility, "selfDeclaredMadeForKids": False},
    }).encode()
    endpoint = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
    request = urllib.request.Request(endpoint, data=metadata, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(video.stat().st_size),
    })
    try:
        with urlopen(request, timeout=90) as response:
            location = response.headers.get("Location")
    except HTTPError as exc:
        raise RuntimeError(f"YouTube rejected upload initialization (HTTP {exc.code})") from exc
    if not location:
        raise RuntimeError("YouTube returned no upload session")
    details = _upload_file(location, video, {"Authorization": f"Bearer {token}",
                                             "Content-Type": "video/mp4"},
                           allow="googleapis.com")
    video_id = details.get("id")
    if not video_id:
        raise RuntimeError("YouTube upload returned no video ID")
    return {"state": "submitted", "remote_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "note": "Uploaded to YouTube; processing/visibility can take additional time."}


def _instagram(video: Path, title: str, description: str) -> dict:
    version = _api_version()
    token = os.environ["CLIPFORGE_INSTAGRAM_TOKEN"]
    ident = os.environ["CLIPFORGE_INSTAGRAM_USER_ID"]
    if not ident.isdecimal():
        raise RuntimeError("Instagram account ID must contain only digits")
    base = f"https://graph.facebook.com/{version}/{ident}"
    created = _json_request(base + "/media", token=token, form={
        "media_type": "REELS", "upload_type": "resumable",
        "caption": (title + "\n" + description).strip()[:2200],
        "share_to_feed": "true",
    })
    container_id = created.get("id")
    if not container_id or not str(container_id).isdecimal():
        raise RuntimeError("Instagram did not create a Reel container")
    upload_url = f"https://rupload.facebook.com/ig-api-upload/{version}/{container_id}"
    _upload_file(upload_url, video, {"Authorization": f"OAuth {token}",
                                     "offset": "0", "file_size": str(video.stat().st_size),
                                     "Content-Type": "application/octet-stream"},
                 allow="rupload.facebook.com")
    import time

    for _ in range(16):
        status = _json_request(
            f"https://graph.facebook.com/{version}/{container_id}?"
            "fields=status_code,status", method="GET", token=token,
        )
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in {"ERROR", "EXPIRED"}:
            raise RuntimeError("Instagram reported an unsuccessful video processing status")
        time.sleep(5)
    else:
        raise RuntimeError("Instagram is still processing; publishing was not attempted")
    published = _json_request(base + "/media_publish", token=token,
                              form={"creation_id": container_id})
    media_id = published.get("id")
    if not media_id:
        raise RuntimeError("Instagram did not confirm Reel publication")
    return {"state": "published", "remote_id": media_id,
            "note": "Instagram accepted the Reels publishing request."}


def _facebook(video: Path, title: str, description: str) -> dict:
    token = os.environ["CLIPFORGE_FACEBOOK_PAGE_ACCESS_TOKEN"]
    version = _api_version()
    endpoint = f"https://graph.facebook.com/{version}/me/video_reels"
    created = _json_request(endpoint, token=token, form={"upload_phase": "start"})
    video_id = created.get("video_id")
    upload_url = created.get("upload_url", "")
    if not video_id or not str(video_id).isdecimal() or not upload_url:
        raise RuntimeError("Facebook did not create a Reel upload session")
    _upload_file(upload_url, video, {"Authorization": f"OAuth {token}",
                                     "offset": "0", "file_size": str(video.stat().st_size),
                                     "Content-Type": "application/octet-stream"},
                 allow="rupload.facebook.com")
    finished = _json_request(endpoint, token=token, form={
        "upload_phase": "finish", "video_id": video_id,
        "video_state": "PUBLISHED", "title": title,
        "description": description,
    })
    if not finished.get("success") and not finished.get("video_id"):
        raise RuntimeError("Facebook did not accept Reel publication")
    return {"state": "submitted", "remote_id": video_id,
            "note": "Facebook accepted the Reel; processing may still be underway."}


def _tiktok_inbox(video: Path) -> dict:
    token = os.environ["CLIPFORGE_TIKTOK_ACCESS_TOKEN"]
    size = video.stat().st_size
    if size <= 0:
        raise ValueError("Empty video")
    chunk_size = min(32 * 1024 * 1024, size)
    count = max(1, size // chunk_size)
    initialized = _json_request("https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
                                token=token, payload={"source_info": {
                                    "source": "FILE_UPLOAD", "video_size": size,
                                    "chunk_size": chunk_size,
                                    "total_chunk_count": count,
                                }})
    if initialized.get("error", {}).get("code") != "ok":
        raise RuntimeError("TikTok rejected the draft upload")
    data = initialized.get("data", {})
    upload_url = data.get("upload_url", "")
    publish_id = data.get("publish_id")
    if not upload_url or not publish_id:
        raise RuntimeError("TikTok did not return a draft upload destination")
    for index in range(count):
        start = index * chunk_size
        length = chunk_size if index < count - 1 else size - start
        _upload_file(upload_url, video, {
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes {start}-{start + length - 1}/{size}",
        }, allow="tiktokapis.com", start=start, count=length)
    return {"state": "sent_to_inbox", "remote_id": publish_id,
            "note": "Open TikTok inbox notifications to review and finish posting."}


def publish(platform: str, video: Path, title: str, description: str,
            visibility: str) -> dict:
    if platform not in SOCIALS:
        raise ValueError("Unknown platform")
    missing = [name for name in SOCIALS[platform]["required"] if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"{SOCIALS[platform]['label']} is not connected. Configure server credentials.")
    if not video.is_file() or video.suffix.lower() != ".mp4":
        raise ValueError("Only rendered MP4 clips can be published")
    if platform == "youtube":
        return _youtube(video, title, description, visibility)
    if platform == "instagram":
        return _instagram(video, title, description)
    if platform == "facebook":
        return _facebook(video, title, description)
    return _tiktok_inbox(video)
