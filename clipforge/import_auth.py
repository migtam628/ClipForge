"""Optional YouTube account-cookie support for authorized source imports.

Cookie contents are never stored in job files, API replies, Docker images or logs.
A private Netscape cookie file is mounted from the Docker host. ClipForge copies
only youtube.com rows to a disposable, writable session file for yt-dlp.
"""
from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

LOGGER = logging.getLogger("uvicorn.error")
COOKIE_ENV = "CLIPFORGE_YOUTUBE_COOKIES_FILE"
DEFAULT_PATH = "/run/clipforge-auth/youtube.txt"
MAX_COOKIE_BYTES = 2 * 1024 * 1024
COOKIE_HEADERS = (b"# Netscape HTTP Cookie File", b"# HTTP Cookie File")


def _is_youtube(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host in {"youtube.com", "youtu.be", "youtube-nocookie.com"} or any(
        host.endswith("." + item)
        for item in ("youtube.com", "youtube-nocookie.com")
    )


def _youtube_cookie_rows() -> list[str] | None:
    path = Path(os.environ.get(COOKIE_ENV) or DEFAULT_PATH)
    if not path.exists():
        if os.environ.get(COOKIE_ENV) and path != Path(DEFAULT_PATH):
            raise RuntimeError(
                "YouTube cookie file was configured but is missing. "
                "Check the private host-file mount and file permissions."
            )
        return None
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("YouTube cookie path must be a regular, non-symlink file")
    try:
        if not 0 < path.stat().st_size <= MAX_COOKIE_BYTES:
            raise RuntimeError("YouTube cookie file is empty or too large")
        data = path.read_bytes()
    except PermissionError as exc:
        raise RuntimeError(
            "ClipForge cannot read the private YouTube cookie file. "
            "Check its group/owner permissions for container user 10001."
        ) from exc
    if not data.startswith(COOKIE_HEADERS):
        raise RuntimeError(
            "YouTube cookies must use Mozilla/Netscape cookies.txt format. "
            "Export only youtube.com cookies; do not use JSON."
        )
    rows = []
    for raw in data.decode("utf-8-sig", errors="replace").splitlines()[1:]:
        if not raw.strip():
            continue
        if raw.startswith("#") and not raw.startswith("#HttpOnly_"):
            continue
        pieces = raw.split("\t")
        if len(pieces) != 7:
            continue
        domain = pieces[0].removeprefix("#HttpOnly_").lstrip(".").lower()
        # Do not copy other website sessions from a broad browser-cookie export.
        if domain == "youtube.com" or domain.endswith(".youtube.com"):
            rows.append(raw)
    if not rows:
        raise RuntimeError(
            "Cookie file has no usable youtube.com entries. "
            "Export cookies for your own authorized YouTube session."
        )
    return rows


def youtube_cookie_status() -> dict:
    """Expose only safe configuration state, never names or cookie values."""
    try:
        rows = _youtube_cookie_rows()
        return {
            "configured": rows is not None,
            "status": "ready" if rows else "not_configured",
        }
    except (RuntimeError, OSError) as exc:
        LOGGER.warning("YouTube cookie configuration is invalid: %s", exc)
        return {"configured": False, "status": "invalid"}


@contextmanager
def youtube_cookie_session(url: str):
    """Yield writable disposable cookiefile only for YouTube URLs."""
    if not _is_youtube(url):
        yield None
        return
    rows = _youtube_cookie_rows()
    if rows is None:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="clipforge-youtube-auth-") as directory:
        file = Path(directory) / "cookies.txt"
        file.write_text(
            "# Netscape HTTP Cookie File\n" + "\n".join(rows) + "\n",
            encoding="utf-8",
        )
        file.chmod(0o600)
        try:
            yield file
        finally:
            # The temporary directory and any yt-dlp cookie updates are deleted.
            LOGGER.info("Cleaned up temporary YouTube cookie session")
