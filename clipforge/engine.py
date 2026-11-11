"""URL validation, highlight scoring, media inspection and FFmpeg rendering."""

from __future__ import annotations

import array
import ipaddress
import json
import math
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

HOSTS = (
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "twitch.tv",
    "clips.twitch.tv",
    "kick.com",
    "vimeo.com",
    "dailymotion.com",
    "tiktok.com",
)


def validate_url(raw_url: str) -> str:
    """Allow a short list of media hosts, not arbitrary network fetches."""
    raw_url = raw_url.strip()
    if len(raw_url) > 2048:
        raise ValueError("URL is too long")
    try:
        parts = urlsplit(raw_url)
        host = (parts.hostname or "").lower().rstrip(".")
        if (
            parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
        ):
            raise ValueError("Use an HTTPS video URL without credentials or a custom port")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("IP-address URLs are not allowed")
        if not any(host == suffix or host.endswith("." + suffix) for suffix in HOSTS):
            raise ValueError("This video host is not supported in the MVP")
    except (ValueError, TypeError) as exc:
        raise ValueError(str(exc)) from exc
    return raw_url


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=40,
        check=True,
    )
    info = json.loads(result.stdout)
    streams = info.get("streams", [])
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise ValueError("Source does not contain a video stream")
    duration = float(info.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Video has no usable duration")
    if duration > 8 * 3600:
        raise ValueError("MVP limit: eight-hour source videos")
    return {"duration": duration, "has_audio": any(s.get("codec_type") == "audio" for s in streams)}


def audio_energy(path: Path, duration: float, sample_rate: int = 4000) -> list[float]:
    """RMS amplitude in roughly 2-second buckets. Not semantic AI scoring."""
    args = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar",
        str(sample_rate), "-f", "s16le", "pipe:1",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    values = []
    try:
        assert proc.stdout is not None
        chunk_bytes = sample_rate * 2 * 2  # 2s of 16-bit PCM
        for _ in range(math.ceil(duration / 2) + 1):
            raw = proc.stdout.read(chunk_bytes)
            if not raw:
                break
            if len(raw) % 2:
                raw = raw[:-1]
            samples = array.array("h")
            samples.frombytes(raw)
            values.append(math.sqrt(sum(v * v for v in samples) / max(1, len(samples))))
    finally:
        if proc.stdout:
            proc.stdout.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return values


def select_highlights(
    duration: float,
    clip_seconds: int,
    max_clips: int,
    energies: list[float] | None = None,
) -> list[dict]:
    """Rank nonoverlapping candidate windows by loudness, output chronologically.

    Selects as many full nonoverlapping clips as budget allows; loudness is a rough
    first-pass candidate ranking, not a claim that clips are meaningful or viral.
    """
    if duration < 3:
        return []
    length = min(float(clip_seconds), duration)
    budget = min(int(duration // length), max_clips) if duration >= length else 1
    budget = max(budget, 1)
    # Half-clip candidate strides let the algorithm favor higher-energy moments.
    stride = max(2.0, length / 2)
    candidates = [round(i * stride, 3) for i in range(int((duration - length) // stride) + 1)]
    last = max(0.0, duration - length)
    if not candidates or last - candidates[-1] > 0.1:
        candidates.append(round(last, 3))

    def score(start: float) -> float:
        if not energies:
            return 0.0
        low = min(len(energies), int(start / 2))
        high = min(len(energies), math.ceil((start + length) / 2))
        window = energies[low:high]
        return sum(window) / len(window) if window else 0.0

    ranked = sorted(candidates, key=lambda start: (-score(start), start))
    chosen: list[float] = []
    for start in ranked:
        if all(abs(start - existing) >= length - 0.001 for existing in chosen):
            chosen.append(start)
        if len(chosen) == budget:
            break
    # If the half-step grid cannot fill the desired count, fill gaps chronologically.
    if len(chosen) < budget:
        for start in [i * length for i in range(int(duration // length))]:
            if all(abs(start - existing) >= length - 0.001 for existing in chosen):
                chosen.append(start)
            if len(chosen) == budget:
                break
    return [
        {"start": round(start, 2), "duration": round(length, 2), "score": round(score(start), 1)}
        for start in sorted(chosen)
    ]


def render_clip(source: Path, dest: Path, start: float, length: float, ratio: str) -> None:
    size = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720)}[ratio]
    w, h = size
    # Preserve the full shot instead of cropping people out of wide footage.
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostdin", "-ss",
            str(start), "-i", str(source), "-t", str(length), "-map", "0:v:0", "-map",
            "0:a:0?", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf",
            "25", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=True,
    )


def render_thumbnail(clip: Path, dest: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostdin", "-ss",
            "0.25", "-i", str(clip), "-frames:v", "1", "-vf", "scale=360:-2",
            str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )


def download_video(url: str, output_dir: Path) -> Path:
    """Fetch only user-authorized content; blocked sites/private content may fail."""
    import yt_dlp

    validate_url(url)
    opts = {
        "outtmpl": str(output_dir / "source.%(ext)s"),
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "max_filesize": 2 * 1024 * 1024 * 1024,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "restrictfilenames": True,
        "extractor_args": {},
    }
    with yt_dlp.YoutubeDL(opts) as downloader:
        downloader.download([url])
    matches = sorted(
        path for path in output_dir.glob("source.*")
        if path.is_file() and path.suffix.lower() not in {".part", ".ytdl", ".json"}
    )
    if not matches:
        raise RuntimeError("No playable file was returned by the source platform")
    source = matches[0]
    if source.stat().st_size > 2 * 1024 * 1024 * 1024:
        raise ValueError("Downloaded video exceeds the 2 GB limit")
    return source
