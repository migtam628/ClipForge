"""URL validation, highlight scoring, media inspection and FFmpeg rendering."""

from __future__ import annotations

import array
import ipaddress
import json
import logging
import math
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from clipforge.formats import OUTPUT_SIZES
from clipforge.import_auth import _is_youtube, youtube_cookie_session

LOGGER = logging.getLogger("uvicorn.error")


@lru_cache(maxsize=1)
def _nvenc_available() -> bool:
    """Prove a real hardware encode works, not merely that FFmpeg lists NVENC."""
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:r=1:d=0.2",
            "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-",
        ], capture_output=True, text=True, timeout=15, check=True)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _run_encoded(prefix: list[str], tail: list[str], dest: Path, *,
                 quality: int, threads: int | None = None) -> None:
    """Use NVENC when available, transparently retry CPU if auto GPU fails."""
    preference = os.environ.get("CLIPFORGE_VIDEO_ENCODER", "cpu").strip().lower()
    if preference not in {"cpu", "auto", "nvenc"}:
        raise ValueError("CLIPFORGE_VIDEO_ENCODER must be cpu, auto, or nvenc")
    gpu = preference != "cpu" and _nvenc_available()
    if preference == "nvenc" and not gpu:
        raise RuntimeError("NVENC requested but no usable NVIDIA encoder was detected")

    def execute(use_gpu: bool) -> None:
        if use_gpu:
            codec = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                     "-cq", str(quality), "-b:v", "0"]
        else:
            codec = ["-c:v", "libx264"]
            if threads is not None:
                codec += ["-threads", str(threads)]
            codec += ["-preset", "veryfast", "-crf", str(quality)]
        subprocess.run(prefix + codec + tail + [str(dest)], capture_output=True,
                       text=True, timeout=600, check=True)

    try:
        execute(gpu)
    except subprocess.CalledProcessError as exc:
        if not gpu or preference != "auto":
            raise
        LOGGER.warning("NVENC render failed (FFmpeg exit %s); retrying with libx264",
                       exc.returncode)
        execute(False)

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


def render_sequence(source: Path, dest: Path, segments: list[dict],
                    *, has_source_audio: bool) -> None:
    """Assemble source excerpts in supplied order, using frame-accurate trims.

    Every segment refers to the same server-owned original video. Re-encoding
    normalizes timestamps across cuts and avoids keyframe-only inaccuracies.
    """
    if not 1 <= len(segments) <= 12:
        raise ValueError("A sequence must contain 1–12 segments")
    total = 0.0
    filters = []
    inputs = []
    for index, item in enumerate(segments):
        start, duration = float(item["start"]), float(item["duration"])
        if not (math.isfinite(start) and math.isfinite(duration)
                and start >= 0 and duration >= 0.5):
            raise ValueError("Invalid timeline segment")
        total += duration
        filters.append(
            f"[0:v:0]trim=start={start:.3f}:duration={duration:.3f},"
            f"setpts=PTS-STARTPTS[v{index}]"
        )
        inputs.append(f"[v{index}]")
        if has_source_audio:
            filters.append(
                f"[0:a:0]atrim=start={start:.3f}:duration={duration:.3f},"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )
            inputs.append(f"[a{index}]")
    if total > 120:
        raise ValueError("A sequence cannot exceed 120 seconds")
    audio_flag = 1 if has_source_audio else 0
    outputs = "[outv][outa]" if has_source_audio else "[outv]"
    filters.append(
        "".join(inputs) +
        f"concat=n={len(segments)}:v=1:a={audio_flag}{outputs}"
    )
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
        "-i", str(source), "-filter_complex", ";".join(filters),
        "-map", "[outv]",
    ]
    if has_source_audio:
        args += ["-map", "[outa]"]
    else:
        args += ["-an"]
    args += ["-t", f"{total:.3f}"]
    _run_encoded(args, ["-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-b:a", "128k", "-movflags", "+faststart"], dest, quality=23)


def render_clip(
    source: Path, dest: Path, start: float, length: float, ratio: str,
    captions: Path | None = None, effect: str = "none", framing: str = "pad",
    voiceover: Path | None = None, has_source_audio: bool = True,
    hook: Path | None = None, smart_track: Path | None = None,
    brand: Path | None = None, context: Path | None = None,
    split_boxes: list[dict[str, float]] | None = None,
    split_ratio: float = 0.5,
    split_fill: str = "cover",
    source_volume: float | None = None, voice_volume: float = 1.0,
    music: Path | None = None, music_volume: float = 0.25,
    music_fade_in: float = 0.5, music_fade_out: float = 0.75,
    music_ducking: bool = True,
    music_start: float = 0,
) -> None:
    if source_volume is None:
        source_volume = 0.18 if voiceover is not None else 1.0
    if not all(math.isfinite(v) and 0 <= v <= 2
               for v in (source_volume, voice_volume, music_volume)):
        raise ValueError("Audio volumes must be finite values between 0 and 2")
    if not all(math.isfinite(v) and 0 <= v <= 5
               for v in (music_fade_in, music_fade_out)):
        raise ValueError("Music fades must be between 0 and 5 seconds")
    if music is not None and music_fade_in + music_fade_out > length:
        raise ValueError("Music fades cannot be longer than the clip")
    if not math.isfinite(music_start) or not 0 <= music_start <= 300:
        raise ValueError("Music start must be between 0 and 300 seconds")
    size = OUTPUT_SIZES[ratio]
    w, h = size
    if framing == "split":
        if ratio != "9:16":
            raise ValueError("Split-screen rendering requires 9:16")
        if not math.isfinite(split_ratio) or not 0.3 <= split_ratio <= 0.7:
            raise ValueError("Split-screen panel ratio must be between 0.3 and 0.7")
        if split_fill not in {"cover", "contain"}:
            raise ValueError("Split-screen fill must be cover or contain")
        top_height = round(h * split_ratio / 2) * 2
        bottom_height = h - top_height
        boxes = split_boxes or [
            {"x": 0.04, "y": 0.21, "width": 0.36, "height": 0.58},
            {"x": 0.60, "y": 0.21, "width": 0.36, "height": 0.58},
        ]
        if len(boxes) != 2:
            raise ValueError("Split-screen requires exactly two crop boxes")
        regions = []
        for box, pane_height in zip(boxes, (top_height, bottom_height)):
            x, y, bw, bh = (float(box[k]) for k in ("x", "y", "width", "height"))
            if not all(math.isfinite(v) for v in (x, y, bw, bh)) or not (
                0 <= x <= 1 and 0 <= y <= 1 and 0.05 < bw <= 1 and 0.05 < bh <= 1
                and x + bw <= 1.000001 and y + bh <= 1.000001
            ):
                raise ValueError("Invalid split-screen crop box")
            crop = (f"crop=w=trunc(iw*{bw:.6f}/2)*2:h=trunc(ih*{bh:.6f}/2)*2:"
                    f"x=iw*{x:.6f}:y=ih*{y:.6f},")
            if split_fill == "cover":
                crop += (f"scale={w}:{pane_height}:force_original_aspect_ratio=increase,"
                         f"crop={w}:{pane_height},setsar=1")
            else:
                crop += (f"scale={w}:{pane_height}:force_original_aspect_ratio=decrease,"
                         f"pad={w}:{pane_height}:(ow-iw)/2:(oh-ih)/2:color=0x152334,setsar=1")
            regions.append(crop)
        vf = (
            "split=2[upper][lower];"
            f"[upper]{regions[0]}[top];"
            f"[lower]{regions[1]}[bottom];"
            "[top][bottom]vstack=inputs=2,setsar=1"
        )
    elif framing == "crop":
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
    if context is not None:
        safe_context = str(context).replace("\\", "\\\\").replace(":", "\\:")
        vf += ",ass=" + safe_context
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
    if music is not None:
        args.extend(["-stream_loop", "-1", "-ss", f"{music_start:.3f}", "-i", str(music)])
    args += ["-t", str(length), "-map", "0:v:0"]
    if voiceover is not None or music is not None:
        # Mix only existing tracks. Music loops under the source and narrator,
        # but the final output is always bounded by -t.
        filters = []
        tracks = []
        ducking = bool(music is not None and music_ducking and
                       (has_source_audio or voiceover is not None))
        duck_keys = []
        if has_source_audio:
            filters.append(f"[0:a:0]volume={source_volume:.3f},asetpts=PTS-STARTPTS" +
                           (",asplit=2[bed][bedkey]" if ducking else "[bed]"))
            tracks.append("[bed]")
            if ducking:
                duck_keys.append("[bedkey]")
        if voiceover is not None:
            filters.append(f"[1:a:0]volume={voice_volume:.3f},asetpts=PTS-STARTPTS" +
                           (",asplit=2[voice][voicekey]" if ducking else "[voice]"))
            tracks.append("[voice]")
            if ducking:
                duck_keys.append("[voicekey]")
        if music is not None:
            index = 2 if voiceover is not None else 1
            chain = (f"[{index}:a:0]volume={music_volume:.3f},"
                     f"atrim=duration={length:.3f},asetpts=PTS-STARTPTS")
            if music_fade_in:
                chain += f",afade=t=in:st=0:d={music_fade_in:.3f}"
            if music_fade_out:
                chain += (f",afade=t=out:st={max(0.0, length - music_fade_out):.3f}:"
                          f"d={music_fade_out:.3f}")
            filters.append(chain + ("[musicraw]" if ducking else "[music]"))
            if ducking:
                if len(duck_keys) > 1:
                    filters.append("".join(duck_keys) +
                                   f"amix=inputs={len(duck_keys)}:normalize=0[duckkey]")
                    key = "[duckkey]"
                else:
                    key = duck_keys[0]
                filters.append(
                    f"[musicraw]{key}sidechaincompress=threshold=0.06:ratio=8:"
                    "attack=20:release=400:makeup=1[music]"
                )
            tracks.append("[music]")
        if len(tracks) == 1:
            filters.append(f"{tracks[0]}anull[aout]")
        else:
            filters.append("".join(tracks) +
                           f"amix=inputs={len(tracks)}:duration=longest:"
                           "dropout_transition=0:normalize=0[aout]")
        audio_filter = ";".join(filters)
        args += ["-filter_complex", audio_filter, "-map", "[aout]"]
    else:
        args += ["-map", "0:a:0?"]
        if has_source_audio and source_volume != 1.0:
            args += ["-af", f"volume={source_volume:.3f}"]
    try:
        threads = max(1, min(8, int(os.environ.get("CLIPFORGE_FFMPEG_THREADS", "2"))))
    except ValueError:
        threads = 2
    args += ["-vf", vf]
    _run_encoded(args, ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart"], dest, quality=25, threads=threads)



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
    # Use the yt-dlp PO-token plugin only for YouTube, and only when the
    # deployment explicitly configures the private provider. Locally installed
    # ClipForge without the sidecar keeps its existing extraction behavior.
    pot_url = os.environ.get("CLIPFORGE_YOUTUBE_POT_URL", "").strip()
    is_youtube = _is_youtube(url)
    if pot_url and is_youtube:
        parsed = urlsplit(pot_url)
        if (parsed.scheme != "http" or parsed.hostname != "yt-pot-provider"
                or parsed.port != 4416 or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError(
                "CLIPFORGE_YOUTUBE_POT_URL must point to the private Docker "
                "service at http://yt-pot-provider:4416"
            )
        opts["extractor_args"] = {
            "youtube": {"player_client": ["mweb"]},
            "youtubepot-bgutilhttp": {"base_url": [pot_url.rstrip("/")]},
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
            if is_youtube and "http error 403" in message:
                raise RuntimeError(
                    "YouTube refused the media download (HTTP 403). "
                    + ("The private PO-token provider was configured, but YouTube "
                       "may still reject the account/session or server IP. "
                       if pot_url else
                       "Configure the private PO-token provider to try a supported "
                       "YouTube client. ")
                    + "Check the token provider logs and YouTube account access. "
                    "For your own video, import the original MP4 instead; "
                    "repeated job resumes alone will not resolve a persistent 403."
                ) from exc
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
