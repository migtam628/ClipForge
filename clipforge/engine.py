"""URL validation, highlight scoring, media inspection and FFmpeg rendering."""

from __future__ import annotations

import array
import ipaddress
import json
import math
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from clipforge.import_auth import youtube_cookie_session

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


def render_clip(
    source: Path, dest: Path, start: float, length: float, ratio: str,
    captions: Path | None = None, effect: str = "none", framing: str = "pad",
    voiceover: Path | None = None, has_source_audio: bool = True,
    hook: Path | None = None, smart_track: Path | None = None,
    brand: Path | None = None,
) -> None:
    size = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720)}[ratio]
    w, h = size
    if framing == "crop":
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1"
        )
    elif framing == "smart":
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        if smart_track is not None and smart_track.is_file():
            safe_track = str(smart_track).replace("\\", "\\\\").replace(":", "\\:")
            vf += f"sendcmd=f={safe_track},"
        vf += f"crop@smart={w}:{h},setsar=1"
    elif framing == "blur":
        vf = (
            "split[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},gblur=sigma=12[back];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[front];"
            "[back][front]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    elif framing == "pad":
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
    else:
        raise ValueError("Unsupported framing")
    effects = {
        "none": "",
        "pop": "eq=contrast=1.12:saturation=1.24,unsharp=5:5:0.6",
        "cinema": "eq=contrast=1.07:saturation=0.86,vignette=PI/5",
        "noir": "hue=s=0,eq=contrast=1.16",
    }
    if effect not in effects:
        raise ValueError("Unsupported video effect")
    if effects[effect]:
        vf += "," + effects[effect]
    if captions is not None:
        # Caption paths are server-owned UUID locations, never user-provided paths.
        safe_path = str(captions).replace("\\", "\\\\").replace(":", "\\:")
        vf += ",ass=" + safe_path
    if hook is not None:
        safe_hook = str(hook).replace("\\", "\\\\").replace(":", "\\:")
        vf += ",ass=" + safe_hook
    if brand is not None:
        safe_brand = str(brand).replace("\\", "\\\\").replace(":", "\\:")
        vf += ",ass=" + safe_brand
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostdin", "-ss",
        str(start), "-i", str(source),
    ]
    if voiceover is not None:
        args.extend(["-i", str(voiceover)])
    args += ["-t", str(length), "-map", "0:v:0"]
    if voiceover is not None:
        # Keep original speech under the commentator without padding infinite audio.
        # FFmpeg -t truncates the final output to the clip length.
        if has_source_audio:
            audio_filter = (
                "[0:a:0]volume=0.18,asetpts=PTS-STARTPTS[bed];"
                "[1:a:0]asetpts=PTS-STARTPTS[voice];"
                "[bed][voice]amix=inputs=2:duration=longest:"
                "dropout_transition=0:normalize=0[aout]"
            )
        else:
            audio_filter = "[1:a:0]asetpts=PTS-STARTPTS[aout]"
        args += ["-filter_complex", audio_filter, "-map", "[aout]"]
    else:
        args += ["-map", "0:a:0?"]
    try:
        threads = max(1, min(8, int(os.environ.get("CLIPFORGE_FFMPEG_THREADS", "2"))))
    except ValueError:
        threads = 2
    args += [
        "-vf", vf, "-c:v", "libx264", "-threads", str(threads),
        "-preset", "veryfast", "-crf",
        "25", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(dest),
    ]
    subprocess.run(args, capture_output=True, text=True, timeout=600, check=True)



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
    # Equivalent to yt-dlp --force-ipv4; scoped only to media imports.
    # Many servers have IPv6 DNS records without a usable IPv6 default route.
    ip_family = os.environ.get("CLIPFORGE_DOWNLOAD_IP_FAMILY", "ipv4").strip().lower()
    families = {"ipv4": "0.0.0.0", "ipv6": "::", "auto": None}
    if ip_family not in families:
        raise ValueError("CLIPFORGE_DOWNLOAD_IP_FAMILY must be ipv4, ipv6, or auto")
    opts = {
        "outtmpl": str(output_dir / "source.%(ext)s"),
        # Prefer <=1080p video/audio, but accept an available resolution if the
        # source has no <=1080p formats. Unknown heights must not be discarded.
        "format": "bv*[height<=?1080]+ba/b[height<=?1080]/bv*+ba/b",
        "format_sort": ["res:1080"],
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
    if families[ip_family] is not None:
        opts["source_address"] = families[ip_family]
    # A mounted private cookie file is optional; never copy it into job storage.
    # Use a writable temporary copy because yt-dlp may update cookies on exit.
    with youtube_cookie_session(url) as cookiefile:
        if cookiefile is not None:
            opts["cookiefile"] = str(cookiefile)
        try:
            with yt_dlp.YoutubeDL(opts) as downloader:
                downloader.download([url])
        except yt_dlp.utils.DownloadError as exc:
            message = str(exc).lower()
            if "requested format is not available" in message or "only images are available" in message:
                raise RuntimeError(
                    "YouTube did not provide a playable video format. ClipForge "
                    "tried its <=1080p preference and unrestricted video fallbacks. "
                    "Confirm this Docker image has Deno and yt-dlp-ejs installed, "
                    "then inspect available formats with yt-dlp -F (see the README). "
                    "If the video still has no usable streams, import an original MP4 "
                    "you are authorized to use, or retry later via Activity."
                ) from exc
            if "network is unreachable" in message:
                raise RuntimeError(
                    "Video source network is unreachable. ClipForge is configured "
                    f"to use {ip_family} for media imports. Check that family from "
                    "the container, then select Resume job in Activity. "
                    "Docker, Tailscale, and other services are unaffected."
                ) from exc
            if "sign in to confirm" in message or "not a bot" in message:
                raise RuntimeError(
                    "YouTube requested account verification. For a video you are "
                    "authorized to download, configure the private youtube.com "
                    "cookies.txt file (see Settings -> YouTube URL imports) "
                    "and select Resume job in Activity. Alternatively, obtain "
                    "the original video through YouTube Studio and upload its MP4. "
                    "Authentication cookies may expire or still be rejected."
                ) from exc
            raise
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
