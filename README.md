# ClipForge

**Turn one authorized long-form video into a batch of short clips.** ClipForge is an early self-hosted, local-first web application for creators who own or have permission to edit their source media.

![Version](https://img.shields.io/badge/version-0.1.0--preview-cce971) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue)

## What works in v0.1.0

- Browser dashboard with source URL or local video upload (MP4, MOV, MKV, WEBM).
- YouTube, Twitch, Kick, Vimeo, Dailymotion, and TikTok URL adapters through `yt-dlp`, **best effort**: availability and download permission vary by platform and source. A listed site does not guarantee every URL works.
- Automatic audio loudness/energy scoring to prioritize candidate segments. If there is no audio, ClipForge creates evenly spaced clips. **This is not semantic AI highlight recognition yet.**
- Up to 100 *non-overlapping* clips per job, with 10–120 second durations; video clips shorter than the requested duration yield one shorter clip. Max source duration eight hours, max file size 2 GB.
- Export MP4 at 9:16, 16:9 or 1:1, retaining the full shot via letterbox/padding.
- Job queue, progress reporting, thumbnail and browser preview, individual downloads, bulk ZIP export, and job deletion.
- Automated unit, API, and real FFmpeg integration tests on Python 3.11 and 3.12, packaging checks, then prerelease publication from `main`.

### Important limitations

ClipForge is an **MVP**. It does not yet offer transcript/semantic AI, animated captions, active-speaker tracking, social publishing, authentication or multi-user controls. Audio energy is a simple proxy for highlights; it cannot determine whether a moment is funny, useful, or compelling. URL ingestion can fail when websites alter their playback systems, restrict downloads, require logins, use DRM, or forbid downloads. You must own the content or have the necessary permissions, and respect platform terms. YouTube's official API policies restrict downloading audiovisual content without prior authorization. **Use source-file upload whenever a platform does not permit video downloads.**

## Run locally

Prerequisites: Python 3.11+, `ffmpeg` and `ffprobe` on `PATH`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m clipforge
```

Open **http://127.0.0.1:8765**. On Windows, activate the virtual environment with `.venv\\Scripts\\activate`.

### Docker Compose

```bash
docker compose up --build -d
```

Open **http://127.0.0.1:8765**. The Docker Compose sample intentionally binds to **localhost only** and stores job files in a persistent Docker named volume (`clipforge_data`).

### Access ClipForge through Tailscale (private HTTPS)

Choose **one** of these deployment methods. ClipForge has no built-in login yet; keep access restricted to trusted tailnet users and devices.

**Option A — Existing Tailscale host (recommended when Tailscale is installed on the Docker machine)**

```bash
git clone https://github.com/migtam628/ClipForge.git
cd ClipForge
docker compose up --build -d
curl -fsS http://127.0.0.1:8765/api/health
tailscale serve --bg 8765
tailscale serve status
```

On another device connected to your tailnet, open the HTTPS address printed by Tailscale Serve, such as `https://<host>.<tailnet>.ts.net/`. You may first need to enable MagicDNS and HTTPS certificates in Tailscale. The ordinary `compose.yaml` intentionally binds port 8765 **only to 127.0.0.1**; leave it that way. On Linux, Tailscale commands may require `sudo`. To stop sharing, use `tailscale serve --https=443 off`. Background Serve resumes after reboot.

**Option B — Tailscale in Docker (dedicated private ClipForge tailnet node)**

This mode creates a Tailscale device named `clipforge` using the official container and `compose.tailscale.yaml`. No host, LAN, or public ports are published. It uses userspace networking, so no `/dev/net/tun` or additional networking capabilities are required.

1. Create a **non-ephemeral** Tailscale auth key using [Tailscale Keys](https://login.tailscale.com/admin/settings/keys). Do not commit or share the key.
2. Copy `.env.example` to `.env`, then replace the example key with your own. `.env` is gitignored.
3. Start the stack:

```bash
git clone https://github.com/migtam628/ClipForge.git
cd ClipForge
cp .env.example .env
# Edit .env to set TS_AUTHKEY to your own key before continuing.
docker compose -f compose.tailscale.yaml up --build -d
docker compose -f compose.tailscale.yaml exec tailscale tailscale status
docker compose -f compose.tailscale.yaml exec tailscale tailscale serve status
```

From another device in the same tailnet, visit the private HTTPS URL reported by Serve (normally `https://clipforge.<tailnet>.ts.net/`). The sidecar proxies HTTPS port 443 to ClipForge port 8765 in their shared network namespace. Tailscale may require MagicDNS and HTTPS to be enabled once. Its device identity survives container restarts via the `tailscale_state` named volume. After initial login, you can remove the auth key from `.env`: `TS_AUTH_ONCE=true` reuses saved state.

Diagnostics and shutdown:

```bash
docker compose -f compose.tailscale.yaml ps
docker compose -f compose.tailscale.yaml logs --tail=100 clipforge tailscale
docker compose -f compose.tailscale.yaml exec tailscale tailscale ip -4
docker compose -f compose.tailscale.yaml exec tailscale tailscale serve status
docker compose -f compose.tailscale.yaml down
```

Do **not** run `docker compose down -v` unless you intend to delete both the Tailscale identity and all ClipForge media/job data. The Tailscale-only deployment has its own Compose project and volumes; do not start both modes simultaneously. Tailscale access controls apply, but default rules may permit every tailnet user; restrict TCP 443 to trusted users. Never use Tailscale Funnel with this unauthenticated preview.
### Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLIPFORGE_HOST` | `127.0.0.1` | Local listener; Docker overrides it to 0.0.0.0 inside the container |
| `CLIPFORGE_PORT` | `8765` | Listener port |
| `CLIPFORGE_DATA_DIR` | `./clipforge-data` | Persistent job inputs and outputs |

**Security:** This preview has no login or authorization system. Do not expose it to the public Internet. Use localhost (or add a trusted authentication gateway first). Video URLs are restricted to selected HTTPS hostnames to reduce network request abuse, but external downloader dependencies should still be treated as untrusted input parsers. Submitted videos can consume significant disk space and CPU. Uploads and downloads are limited to 2 GB; the queue runs a single job at a time in one process. Don't start `uvicorn` with multiple worker processes.

## API

Interactive OpenAPI docs at `/api/docs`.

- `POST /api/jobs` with JSON `{ "url": "https://...", "rights_confirmed": true, "clip_seconds": 30, "max_clips": 25, "aspect_ratio": "9:16" }`
- `POST /api/uploads` with multipart `file`, `rights_confirmed`, `clip_seconds`, `max_clips`, `aspect_ratio`
- `GET /api/jobs`, `GET /api/jobs/{id}`
- `GET /api/jobs/{id}/clips/{filename}`, `GET /api/jobs/{id}/export`
- `DELETE /api/jobs/{id}` when completed or failed
- `GET /api/health`

## Tests and releases

```bash
pip install -e '.[dev]'
ruff check .
pytest -q
python -m build
```

`.github/workflows/ci-release.yml` tests pull requests and `main` with Python 3.11 and 3.12, including actual FFmpeg processing of generated test footage. If all tests pass and the wheel/sdist builds, a push to `main` automatically publishes a GitHub **prerelease** named `v0.1.0-preview.<run number>` with distribution files attached. Failed tests block releases. Review the run on **Actions**, and download packaged source/wheel files from **Releases**. A release is not a deployed cloud instance; ClipForge still runs on your own machine/server.

### Current architecture

```text
Browser UI (HTML/CSS/JS)
  -> FastAPI -> in-process single-worker queue
     -> URL adapter (yt-dlp) / uploaded file
     -> FFprobe -> FFmpeg audio decode -> RMS candidate scoring
     -> FFmpeg MP4 rendering and thumbnails
     -> local storage -> preview / download / ZIP
```

## Roadmap

1. Speech-to-text, sentence-aware clip boundaries and optional transcript captions.
2. Semantic highlight scoring (hook, reaction, punchline, audience engagement) alongside loudness.
3. Automatic speaker framing, scene-aware vertical crop, logo and subtitle presets.
4. Authenticated users, resumable jobs, durable distributed queue, storage quotas and retention.
5. Optional authorized publishing integrations for YouTube Shorts, TikTok, Instagram Reels.

## License

MIT. See [LICENSE](LICENSE).