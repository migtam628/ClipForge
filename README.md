# ClipForge

**AI-powered self-hosted video-to-shorts studio.** Turn video files or authorized video links into ranked clips with burned-in captions, effects, previews and creator-approved social publishing.

![Preview](https://img.shields.io/badge/version-0.3.0--preview-d4f268) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue)

## What it does

- Sources: uploaded MP4, MOV, MKV or WEBM (up to 2 GB), or permitted HTTPS links via best-effort `yt-dlp` adapters for YouTube, Twitch, Kick, Vimeo, Dailymotion and TikTok. **Only use content you own or are authorized to download and edit.** YouTube API policies restrict downloading audiovisual content without authorization.
- **AI highlight selection**: local `faster-whisper` transcribes speech with word timestamps and Ollama scores transcript windows for hooks, useful insights, stories and punchlines. The AI picks from bounded video timestamps, returns titles and reasons, and won't silently masquerade as AI when Ollama is offline. Source length limit: 8 hours; up to 100 clips of 10–120 seconds each.
- **AI-assisted editing**: automatic timed captions (classic white, bold yellow, neon pink); color/effect presets (natural, vibrant pop, cinematic, black-and-white); optional crop-to-fill or full-frame padding at 9:16, 16:9 or 1:1. FFmpeg/libass creates the actual MP4 output. AI ranking and clipping remain imperfect; verify each clip before posting.
- **Creator review**: browse job progress, previews, thumbnails, captions/effects badges, and AI-generated titles, then download MP4 or bulk ZIP.
- **Social publishing**: after reviewing each clip, explicitly authorize transfer to connected YouTube Shorts, Instagram Reels and Facebook Page Reels, or send a draft to the TikTok inbox for final posting. Platforms may restrict access or require developer approval.

## YouTube account verification on authorized imports

Sometimes yt-dlp reports **"Sign in to confirm you're not a bot"** when
YouTube requires an authenticated browser session. This is a YouTube request
block, not an AI, FFmpeg or hardware problem. Repeating it automatically
will usually not help. **Only download and edit videos when you have the
necessary permissions and YouTube permits the download.**

**Safest approach for your own videos:** obtain the original MP4 from
YouTube Studio (or your original camera/source), then use Studio → Upload file.

For a video you have permission to retrieve through yt-dlp, optional
authentication is supported using a YouTube-only Netscape `cookies.txt` file.
The official yt-dlp FAQ and Extractors wiki describe exporting these cookies.
Some accounts may still be blocked, and cookies may become invalid or rotate.

1. Export **only `youtube.com` cookies** in Netscape format on your own
   trusted computer, from a session allowed to access the video. Do not
   export all browser sessions. Do not share this file with ChatGPT,
   GitHub, email, or anybody else: it may grant access to your account.
2. Put the file at `~/ClipForge/private-auth/youtube.txt` on the Ubuntu
   Docker host (for example using `scp` over your tailnet). The
   `private-auth` folder is excluded from Git **and** Docker build context.
3. On Ubuntu, ensure group 10001 (the ClipForge container user) can read it:

```bash
cd ~/ClipForge
mkdir -p private-auth
# Copy your YouTube-only Netscape cookie export into private-auth/youtube.txt
sudo chgrp 10001 private-auth private-auth/youtube.txt
chmod 750 private-auth
chmod 640 private-auth/youtube.txt
docker compose --profile ai up -d --force-recreate clipforge
docker compose --profile ai exec clipforge python -c \
  'from clipforge.import_auth import youtube_cookie_status; print(youtube_cookie_status())'
```

4. Confirm **Settings → YouTube URL imports** says the file is ready.
   Return to **Activity → Resume job** to retry the failed URL download.

ClipForge mounts `./private-auth` into its container **read-only**, copies
only YouTube cookie entries into a temporary writable file per import,
and deletes it after the attempt. It never includes cookies in the
job JSON, API response, Git history or Docker image. The folder can be
left empty when you prefer direct video uploads. When the login expires,
replace the host file yourself. Do not expose your ClipForge instance
outside trusted Tailscale access.

See [yt-dlp cookie instructions](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
and [YouTube Extractors guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors).

## Workspace navigation

ClipForge now has **five distinct page URLs** with real navigation:

- `/studio` - import video, select 1/2/3/5/10/25/50/100 or a custom clip limit, configure edits and optional commentary.
- `/library` - view completed clips, read/copy narrator scripts, preview and download.
- `/publishing` - see platform connection status, review and explicitly approve each upload.
- `/activity` - processing queue, stage progress, error diagnostics and Resume job.
- `/settings` - persistent non-secret Studio defaults, AI/voice status, publishing connections and storage/security guidance.

Rendering defaults to two FFmpeg encoder threads to reduce memory/CPU contention with Ollama on small servers (adjust `CLIPFORGE_FFMPEG_THREADS` if needed).

Settings save to the persistent `clipforge_data` Docker volume. New jobs inherit
the current Studio defaults; existing jobs and previously generated clips are not changed.
API limits remain 1-100 clips, with 1/3/5 among the new visual presets.

## AI commentator and narrated clips

Choose one of three narration modes in **Studio**:

1. **Original audio only** - no script and no TTS usage.
2. **Generate AI script** - Ollama writes a short English or Spanish commentary
   derived from the actual speech transcript in each selected video segment.
   The script appears in the Library; no external voice service is called.
3. **AI script + commentator voice** - also sends the generated script to
   ElevenLabs text-to-speech, mixes it into the video with quiet original
   audio (18% of source), and burns approximate script-aligned captions when
   captions are enabled. Style can be Energetic, Documentary, or Casual.

Configure voiceover with an ElevenLabs API key and an authorized voice ID
from your account. Add to the untracked server `.env`:

```env
CLIPFORGE_ELEVENLABS_API_KEY=YOUR_PERSONAL_API_KEY
CLIPFORGE_ELEVENLABS_VOICE_ID=YOUR_AUTHORIZED_VOICE_ID
CLIPFORGE_ELEVENLABS_MODEL=eleven_multilingual_v2
```

Do not paste real credentials into GitHub, screenshots or this chat.
Recreate the ClipForge container after editing `.env`:
`docker compose --profile ai up -d --force-recreate clipforge`.
The Studio will display whether ElevenLabs is connected. Use Script-only mode
until voiceover is configured. ElevenLabs requests require Internet access,
can incur provider charges, and are **not automatically retried** after ambiguous
network failures to avoid duplicate billable requests. Source media stays local;
only generated narration text is transmitted to ElevenLabs when voiceover is
explicitly enabled. Ollama uses the local Docker network.

**Editorial limitations:** Generated scripts summarize the selected spoken
excerpt, not independent fact-checked research. The model can still make
mistakes; review scripts and videos before publishing. TTS caption timestamps
are approximate, not word-level aligned. Very long narrations may be
trimmed to clip duration. Existing completed jobs are not automatically
rewritten or re-narrated by this release.

The direct API contract follows the
[ElevenLabs text-to-speech endpoint](https://elevenlabs.io/docs/api-reference/text-to-speech/convert).

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

ClipForge uses server-side credentials. **Browser-based account OAuth connection and token refresh management screens are not implemented yet.** Obtain approved credentials from your own platform developer applications. Add the matching variables to `.env` (never commit it); Compose maps them to the ClipForge process. Recreate the ClipForge container to apply changes: `docker compose --profile ai up -d --force-recreate clipforge` (or the matching Tailscale Compose command).

| Destination | Supported result | Environment variables |
| --- | --- | --- |
| YouTube / Shorts | Official resumable upload via `videos.insert`; defaults to **private**, gives video URL once submitted | `CLIPFORGE_YOUTUBE_CLIENT_ID`, `CLIPFORGE_YOUTUBE_CLIENT_SECRET`, `CLIPFORGE_YOUTUBE_REFRESH_TOKEN` (OAuth `youtube.upload` grant) |
| Instagram Reels | Professional-account resumable upload, processing check, then publish | `CLIPFORGE_INSTAGRAM_TOKEN`, `CLIPFORGE_INSTAGRAM_USER_ID` (Meta Instagram publishing permissions) |
| Facebook Page Reels | Page Reel session, binary upload, finish request | `CLIPFORGE_FACEBOOK_PAGE_ACCESS_TOKEN` (Page posting permissions) |
| TikTok | **Inbox draft only**; user finishes publication in TikTok | `CLIPFORGE_TIKTOK_ACCESS_TOKEN` (`video.upload` scope / approved developer app) |
| Other platforms | Download MP4/ZIP, then manually upload | None |

Connect to [Google Cloud](https://console.cloud.google.com/), [Meta for Developers](https://developers.facebook.com/) or [TikTok for Developers](https://developers.tiktok.com/) to configure the corresponding OAuth apps, scopes and consent. Existing API tokens/refresh tokens must be provisioned by the account owner; never paste them into ClipForge's public repository. Instagram/Facebook live integration can vary by permissions/account mode/API versions. For TikTok, **sent_to_inbox is not published**. YouTube/Facebook return **submitted** because site-side processing can still be in progress; Instagram returns **published** only once the publish endpoint returns a media ID. Do not assume a successful binary upload means the platform published the video.

Every post requires review and a separate confirmation. **There is no automatic cross-posting without approval, scheduling, public URL hosting, or bulk auto-publish.** Even though Tailscale remains private, Instagram's resumable API accepts uploaded binary media; its URL-based variant is not used. Publishing integrations are beta: CI tests validate request structure using mocks, **not live account access or real external publication**.

## Automatic retries and checkpoint recovery

ClipForge now checkpoints completed source media, audio-energy analysis, Whisper
transcripts, each successful Ollama scoring batch, selected candidates, and
individual rendered MP4 clips in its persistent media volume.

- On transient Ollama failures, ClipForge retries each scoring request up to three
  times with exponential backoff. A smaller default batch (four excerpts) and
  a 600-second request timeout help CPU-only mini servers.
- For failed jobs, select **Resume job** in the Clip Library, or call
  `POST /api/jobs/{id}/resume`. It will reuse saved results and only redo
  incomplete stages, keeping the original job ID.
- Jobs interrupted by a container restart automatically requeue at startup.
  Failed jobs are not automatically restarted forever; resume them manually.
- Model changes invalidate AI score checkpoints. Browser uploads interrupted
  mid-upload must be submitted again; Whisper transcription is checkpointed
  when fully complete, not paragraph-by-paragraph. Invalid source media and
  certificate failures cannot be repaired just by retrying.

Optional settings in .env (restart/recreate ClipForge after editing):

```env
CLIPFORGE_AI_BATCH_SIZE=4
CLIPFORGE_AI_TIMEOUT=600
CLIPFORGE_AI_RETRIES=3
CLIPFORGE_FFMPEG_THREADS=2
```

Run your existing host-Tailscale deployment with
`docker compose --profile ai up -d --build --remove-orphans`.
Keep Docker volumes; do not run `docker compose down -v`.
## API

API schema: `/api/docs`.

- `POST /api/jobs`: `{ "url": "https://...", "rights_confirmed": true, "clip_seconds": 30, "max_clips": 25, "aspect_ratio": "9:16", "selection_mode": "ai", "caption_style": "bold", "effect": "pop", "framing": "crop" }`
- Optional job fields: `"narration": "none" | "script" | "narrated"`, `"narration_style": "energetic" | "documentary" | "casual"`, `"narration_language": "en" | "es"`.
- `POST /api/uploads`: multipart file plus the same options, separately.
- `GET /api/jobs`, `/api/jobs/{id}`, `/api/ai/status`, `/api/publishing/platforms`.
- `GET /api/settings` returns safe defaults and config status; `PUT /api/settings` saves validated non-secret `Options`.
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

GitHub Actions checks Python 3.11/3.12, FFmpeg integration media renders, mocked publishing adapters, Docker Compose syntax, package artifacts and a Docker smoke test. A push to `main` publishes `v0.3.0-preview.<run-number>` **only after** tests and image checks succeed. External publishing is not exercised by CI. Check [Actions](https://github.com/migtam628/ClipForge/actions) and [Releases](https://github.com/migtam628/ClipForge/releases).

## Security and limits

This is a private single-user preview, not a public multi-tenant SaaS. Keep Tailscale access limited. Tokens live in server environment only and are not returned by the status API. Downloaded/uploaded videos are parsed by third-party media tooling, so treat sources carefully. Uploaded files are limited to 2 GB and source duration to 8 hours. Media jobs use a single-process worker and publishing uses a separate worker; do not start Uvicorn with multiple workers. `clipforge_data` is a persistent Docker volume. AI predictions, caption timing and visual framing require human review before posting.

## Roadmap

1. AI scene segmentation, sentence-aware trims, speaker/face tracking, animated captions and brand presets.
2. Native OAuth account connection, account switching, token refresh UI and per-platform upload status verification.
3. Publishing queues, drafts, approval workflows and optional scheduled posting **with explicit creator authorization**.
4. Durable distributed queues, media retention policies, storage quotas and user authentication.

MIT license; see [LICENSE](LICENSE).
