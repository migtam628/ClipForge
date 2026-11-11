# ClipForge

**AI-powered self-hosted video-to-shorts studio.** Turn video files or authorized video links into ranked clips with burned-in captions, effects, previews and creator-approved social publishing.

![Preview](https://img.shields.io/badge/version-0.2.0--preview-d4f268) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue)

## What it does

- Sources: uploaded MP4, MOV, MKV or WEBM (up to 2 GB), or permitted HTTPS links via best-effort `yt-dlp` adapters for YouTube, Twitch, Kick, Vimeo, Dailymotion and TikTok. **Only use content you own or are authorized to download and edit.** YouTube API policies restrict downloading audiovisual content without authorization.
- **AI highlight selection**: local `faster-whisper` transcribes speech with word timestamps and Ollama scores transcript windows for hooks, useful insights, stories and punchlines. The AI picks from bounded video timestamps, returns titles and reasons, and won't silently masquerade as AI when Ollama is offline. Source length limit: 8 hours; up to 100 clips of 10–120 seconds each.
- **AI-assisted editing**: automatic timed captions (classic white, bold yellow, neon pink); color/effect presets (natural, vibrant pop, cinematic, black-and-white); optional crop-to-fill or full-frame padding at 9:16, 16:9 or 1:1. FFmpeg/libass creates the actual MP4 output. AI ranking and clipping remain imperfect; verify each clip before posting.
- **Creator review**: browse job progress, previews, thumbnails, captions/effects badges, and AI-generated titles, then download MP4 or bulk ZIP.
- **Social publishing**: after reviewing each clip, explicitly authorize transfer to connected YouTube Shorts, Instagram Reels and Facebook Page Reels, or send a draft to the TikTok inbox for final posting. Platforms may restrict access or require developer approval.

## Docker (recommended)

Prerequisites: Docker Engine with Compose v2. Docker publishes ClipForge on **localhost only**, not publicly.

```bash
git clone https://github.com/migtam628/ClipForge.git
cd ClipForge
docker compose --profile ai up --build -d
docker compose exec ollama ollama pull llama3.2:3b
curl -fsS http://127.0.0.1:8765/api/health
```

Open `http://127.0.0.1:8765`. AI is the browser's default mode; **starting Ollama and pulling its model is required** for AI selection. The first transcription downloads the selected Whisper model and long videos can require substantial CPU time and disk space. The default models are `CLIPFORGE_WHISPER_MODEL=base` and `CLIPFORGE_AI_MODEL=llama3.2:3b`. `--profile ai` starts the Ollama sidecar with persistent models in the Docker volume `ollama_models`. No Ollama port is exposed to the host. To use the earlier heuristic-only mode, `docker compose up --build -d` works; choose Audio-energy and No captions in the browser.

For native Python 3.11+, install FFmpeg/FFprobe and run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[ai]'
export CLIPFORGE_OLLAMA_URL=http://127.0.0.1:11434
python -m clipforge
```

A separate local Ollama service with the selected model must be running for AI mode. Native Python starts bound to localhost by default. Docker listens on 0.0.0.0 *inside* its container, but `compose.yaml` binds only 127.0.0.1 on the host.

## Private Tailscale deployment

Choose **one** option. ClipForge currently lacks built-in user authentication; only trusted tailnet members should be allowed access. Never use Funnel or expose the application publicly.

**A. Tailscale installed on the Docker host (simplest)**

```bash
cd ClipForge
docker compose --profile ai up --build -d
docker compose exec ollama ollama pull llama3.2:3b
tailscale serve --bg 8765
tailscale serve status
```

Visit the Tailscale HTTPS hostname reported by `tailscale serve status` from another tailnet device. You may need MagicDNS/HTTPS enabled. On some Linux hosts, use `sudo tailscale ...`.

**B. Dedicated Tailscale container / tailnet device**

Create a **non-ephemeral** Tailscale auth key at https://login.tailscale.com/admin/settings/keys and store it in the untracked `.env` file:

```bash
cd ClipForge
cp .env.example .env
nano .env  # replace TS_AUTHKEY=tskey-auth-REPLACE_ME

docker compose -f compose.tailscale.yaml --profile ai up --build -d
docker compose -f compose.tailscale.yaml exec ollama ollama pull llama3.2:3b
docker compose -f compose.tailscale.yaml exec tailscale tailscale serve status
```

Both ClipForge and its AI service remain private to Docker/Tailscale. Tailscale configuration is in `tailscale/serve.json`; the node identity is persisted in `tailscale_state`. When the node is authenticated, `TS_AUTH_ONCE=true` reuses its identity on restarts. Never run `docker compose down -v` unless you intentionally want to delete all jobs and Tailscale/AI state. After setup, remove the auth key from `.env` if possible. Access controls on your tailnet still apply; restrict them appropriately.

## Connect social publishing

ClipForge 0.2 uses server-side credentials. **Browser-based account OAuth connection and token refresh management screens are not implemented yet.** Obtain approved credentials from your own platform developer applications. Add the matching variables to `.env` (never commit it); Compose maps them to the ClipForge process. Recreate the ClipForge container to apply changes: `docker compose --profile ai up -d --force-recreate clipforge` (or the matching Tailscale Compose command).

| Destination | Supported result | Environment variables |
| --- | --- | --- |
| YouTube / Shorts | Official resumable upload via `videos.insert`; defaults to **private**, gives video URL once submitted | `CLIPFORGE_YOUTUBE_CLIENT_ID`, `CLIPFORGE_YOUTUBE_CLIENT_SECRET`, `CLIPFORGE_YOUTUBE_REFRESH_TOKEN` (OAuth `youtube.upload` grant) |
| Instagram Reels | Professional-account resumable upload, processing check, then publish | `CLIPFORGE_INSTAGRAM_TOKEN`, `CLIPFORGE_INSTAGRAM_USER_ID` (Meta Instagram publishing permissions) |
| Facebook Page Reels | Page Reel session, binary upload, finish request | `CLIPFORGE_FACEBOOK_PAGE_ACCESS_TOKEN` (Page posting permissions) |
| TikTok | **Inbox draft only**; user finishes publication in TikTok | `CLIPFORGE_TIKTOK_ACCESS_TOKEN` (`video.upload` scope / approved developer app) |
| Other platforms | Download MP4/ZIP, then manually upload | None |

Connect to [Google Cloud](https://console.cloud.google.com/), [Meta for Developers](https://developers.facebook.com/) or [TikTok for Developers](https://developers.tiktok.com/) to configure the corresponding OAuth apps, scopes and consent. Existing API tokens/refresh tokens must be provisioned by the account owner; never paste them into ClipForge's public repository. Instagram/Facebook live integration can vary by permissions/account mode/API versions. For TikTok, **sent_to_inbox is not published**. YouTube/Facebook return **submitted** because site-side processing can still be in progress; Instagram returns **published** only once the publish endpoint returns a media ID. Do not assume a successful binary upload means the platform published the video.

Every post requires review and a separate confirmation. **There is no automatic cross-posting without approval, scheduling, public URL hosting, or bulk auto-publish.** Even though Tailscale remains private, Instagram's resumable API accepts uploaded binary media; its URL-based variant is not used. Publishing integrations are beta: CI tests validate request structure using mocks, **not live account access or real external publication**.

## API

API schema: `/api/docs`.

- `POST /api/jobs`: `{ "url": "https://...", "rights_confirmed": true, "clip_seconds": 30, "max_clips": 25, "aspect_ratio": "9:16", "selection_mode": "ai", "caption_style": "bold", "effect": "pop", "framing": "crop" }`
- `POST /api/uploads`: multipart file plus the same options, separately.
- `GET /api/jobs`, `/api/jobs/{id}`, `/api/ai/status`, `/api/publishing/platforms`.
- `GET /api/jobs/{id}/clips/{filename}`; `GET /api/jobs/{id}/export`.
- `POST /api/jobs/{id}/publish`: `{ "filename": "clip-001.mp4", "platform": "youtube", "title": "My short", "description": "#shorts", "visibility": "private", "approved": true }`. Requires a completed job, a clip that belongs to it, and configured server credentials.
- `DELETE /api/jobs/{id}` (only when processing/publishing is not ongoing).

API defaults remain backward compatible (energy selection/no captions); browser defaults opt into AI editing. An API request for `selection_mode="ai"` fails transparently if the model service is unavailable. Unknown or unsupported media links and DRM-protected streams cannot be guaranteed to work.

## Tests and releases

```bash
pip install -e '.[dev]'
ruff check .
pytest -q
python -m build
```

GitHub Actions checks Python 3.11/3.12, FFmpeg integration media renders, mocked publishing adapters, Docker Compose syntax, package artifacts and a Docker smoke test. A push to `main` publishes `v0.2.0-preview.<run-number>` **only after** tests and image checks succeed. External publishing is not exercised by CI. Check [Actions](https://github.com/migtam628/ClipForge/actions) and [Releases](https://github.com/migtam628/ClipForge/releases).

## Security and limits

This is a private single-user preview, not a public multi-tenant SaaS. Keep Tailscale access limited. Tokens live in server environment only and are not returned by the status API. Downloaded/uploaded videos are parsed by third-party media tooling, so treat sources carefully. Uploaded files are limited to 2 GB and source duration to 8 hours. Media jobs use a single-process worker and publishing uses a separate worker; do not start Uvicorn with multiple workers. `clipforge_data` is a persistent Docker volume. AI predictions, caption timing and visual framing require human review before posting.

## Roadmap

1. AI scene segmentation, sentence-aware trims, speaker/face tracking, animated captions and brand presets.
2. Native OAuth account connection, account switching, token refresh UI and per-platform upload status verification.
3. Publishing queues, drafts, approval workflows and optional scheduled posting **with explicit creator authorization**.
4. Durable distributed queues, media retention policies, storage quotas and user authentication.

MIT license; see [LICENSE](LICENSE).
