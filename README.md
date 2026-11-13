# ClipForge

**AI-powered self-hosted video-to-shorts studio.** Turn video files or authorized video links into ranked clips with burned-in captions, effects, previews and creator-approved social publishing.

![Preview](https://img.shields.io/badge/version-0.10.0--preview-d4f268) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue)

## Guided Studio and two-person reaction framing

The Studio's four-step creation flow organizes existing capabilities as **Your
video → Framing → Style & moments → Review & create**. The interface is inspired
by modern video-clipping workflows while retaining ClipForge's own appearance,
local processing and explicit permission checks. Choose a video URL or authorized
file, pick a layout, customize AI moment discovery and captions, then queue the
job. All jobs still run through the same persistent worker, and all generated
results remain in the **Library** for review and download. Nothing is published
automatically.

**Split in two** is a real FFmpeg rendering layout for 9:16 reaction or
interview clips: the upper and lower panels are separately cropped from two
user-positioned source rectangles. Choose a single local file to see a live
preview, drag the two crop boxes over the people, and adjust their zoom sliders.
The normalized crop coordinates are validated, saved with the job and applied
to every rendered clip. For a URL or batch, the original footage is unavailable
until downloaded: framing is saved, but the setup-time preview cannot show the
remote video. Position boxes based on a local copy if precision is important.

Other layouts remain available: smart face-aware framing, centered crop,
blurred background and full-frame padding. Six caption styles are selectable
from visual examples; their sample words are illustrative, while rendered
captions come from actual recognized speech. Clip discovery and narrated scripts
still require working local AI models. A YouTube HTTP 403 remains a separate
import failure, not a framing problem: use a file you have rights to edit if
the original provider rejects downloading.

For local UI iteration, run `npm run dev` with `uv` installed. The optional
no-dependency Node wrapper serves the **real FastAPI UI and API** at port 4173;
it is not required for the Docker production deployment.

## Professional social-video reframing and word-aligned captions (v0.5)

**Studio → Vertical framing** now supports smart face-aware crop and blurred
background in addition to centered crop and padding. Smart tracking samples at
most 32 frames per clip with OpenCV's CPU Haar face detector, smooths the target
across samples and instructs FFmpeg to move the crop. If no face is detected,
it safely falls back to centered framing. This is basic face-aware tracking,
not identity recognition, lip-sync speaker attribution, or guaranteed tracking
of multiple speakers. Raw face images/biometric templates are not retained.

**Automatic captions** now include two animated styles:

- **Karaoke** uses per-word timestamps from faster-whisper to progressively
  highlight words in four-word phrases.
- **Pop** shows individual words with a brief scale-in animation.

Both also work in the clip revision editor. Narration-script captions use
estimated timing, **not** provider-exact alignment. On resource-limited
hardware, start with one 15-second clip and compare smart crop and blurred
background; automatic face tracking may increase processing time. The
server's output remains review-required before publishing.

OpenCV is installed in the Docker AI image via the headless Python package,
which does not require an X11 desktop. The `smart` and `blur` options do
not use cloud GPU services, send face data to external providers, or alter
YouTube authentication or networking.

## Batch workflows (v0.6)

**Studio** now supports multiple uploaded videos, folder selection (Chrome and
other browsers with directory upload support), and multiline source URLs.
Each item becomes its own persistent job, with source media, transcription,
AI scores and finished clips checkpointed individually. The server runs one
video-processing worker at a time to keep CPU/RAM predictable.

- Add up to **100 source videos per batch**, with an **8 GB aggregate upload
  limit**, **2 GB per video**, and no more than **125 pending/active sources**.
  These are different from the existing "1–100 clips per video" setting.
- Select a folder of MP4, MOV, MKV or WebM files, or use the multi-file picker.
  Other file extensions are omitted by the browser client and rejected by
  the API if submitted manually.
- Paste one URL per line in Studio. Duplicate URLs within the request are
  removed before jobs are created.
- Go to **Activity** for per-source status and batch completion counts. Failed
  sources can be retried individually without rerunning completed sources.
- Narrated batches require **explicit approval** for potentially paid
  ElevenLabs requests in addition to source-rights confirmation.
- Upload failures roll back staged job folders before any processing begins.
  Existing jobs and completed clips remain untouched.

API: `POST /api/jobs/batch` (JSON `urls` plus clip options), `POST
/api/uploads/batch` (multipart repeated `files` plus options), and
`GET /api/batches` (summary counts). This is sequential batch processing,
not simultaneous 100-video encoding or a distributed worker cluster.

## Multi-platform scheduling and presets (v0.7)

Open **Publishing** and select a completed MP4. You can still send the video
manually or choose **Schedule to multiple platforms**. Only destinations
with server-side credentials configured are offered; this does **not** mean
that OAuth tokens or app permissions have been independently verified.

For scheduled posts, you must explicitly select the destinations, title,
visibility, and a local date/time, and confirm that ClipForge may perform
the external transfer later. The API accepts a timezone-aware instant from
1 minute to 30 days ahead. The single outbound worker checks scheduled posts
approximately every 15 seconds while ClipForge is running, dispatching
each approved destination separately. Schedules persist across Docker restarts.
TikTok's API integration sends an **inbox draft**, not a published TikTok post.

Save up to ten non-secret **publishing presets** for reusable platform sets,
description/hashtags and visibility. Credentials remain only in the server
environment.

Publication records distinguish `scheduled`, `queued`, `uploading`,
`published`, `sent_to_inbox`, `failed`, and `needs_review`.
If the container stops with an in-progress upload, ClipForge **does not**
automatically retry it: the remote platform may have accepted the media.
First check your account, then explicitly acknowledge the duplicate-post
risk before retrying. Future schedules can be canceled up until dispatch.

Existing uploaded video versions remain immutable after a clip edit.
A scheduled post always references the exact MP4 chosen at approval time.
Publishing is best-effort via authorized official APIs and may require
additional platform permissions, account verification or review.
Do not assume success without reviewing the provider-side result.

New API routes: `POST /api/jobs/{id}/schedule`, `GET
/api/publishing/queue`, `POST /api/jobs/{id}/publications/{pub_id}/cancel`,
`POST /api/jobs/{id}/publications/{pub_id}/reapprove` (explicit duplicate
check), and `GET/PUT /api/publishing/presets`.

## Creator analytics, clipping campaigns and reported earnings (v0.8)

The new **Analytics** page provides a durable performance ledger for clips
published through ClipForge **or** an external editor. Enter the metrics you
actually see in a platform's dashboard: views, likes, comments, shares, saves,
and **actual USD payouts received**. Save campaign/program names, the
submission status (`submitted`, `approved`, `rejected`, `paid`),
a post URL, an evidence link, and notes.

- All metrics are **self-reported manual snapshots**, not remotely verified,
  inferred, scraped or auto-synced from social networks. The UI displays
  this provenance prominently. The dashboard does **not** estimate payouts.
- Each new snapshot for the same platform/post supersedes the previous
  snapshot in headline totals; earlier snapshots remain in history. This
  prevents double-counting views and earnings.
- Attach a snapshot to a saved clip or to an external HTTPS post URL.
  A platform-specific post identity prevents cross-platform merging.
- Summary cards show tracked posts, reported views/engagements, and actual
  self-reported payouts. Platform breakdowns and campaign outcome counts
  are computed only from the latest submitted snapshots.
- Saved ClipForge publication states are shown separately; a provider
  "published" response is not treated as evidence of views or income.
- Export all records as CSV with spreadsheet-formula safeguards; delete
  mistaken snapshots without deleting your videos. Stored in the existing
  ClipForge data volume, capped at 5,000 snapshots.

API: `POST/GET /api/analytics/records`, `DELETE
/api/analytics/records/{id}`, `GET /api/analytics/summary`, `GET
/api/analytics/export`. Adding automatic API-driven performance sync will
require provider permission review and verified account connections in a
future phase. No fake metrics or projected revenue have been added.

## Full-clip video-grounded narration (v0.9)

The old one-paragraph commentator script used only the transcript and could
finish long before the clip. In **Studio → Commentator & storytelling**, choose
**Video frames + spoken words** and **Generate AI script** or **AI script +
commentator voice**. The new workflow:

1. Divides each selected 3–120 second clip into **2–8 timed sections** covering
   the entire clip, from beginning to end.
2. Uses Whisper to gather the **spoken words for each section**, then uses a
   local Ollama vision model to inspect **one sampled still frame per section**.
   Frames are kept in memory and not saved or sent to ElevenLabs.
3. Asks the local language model to write a distinct narration beat for each
   time section, constrained to evidence from that section. Sections with
   insufficient evidence remain quiet rather than making up events.
4. Saves a **reviewable timed script** in Library: e.g. 00:00–00:08,
   00:08–00:15, etc. The script remains available even when narration audio
   is not requested.
5. In **narrated** mode, synthesizes one voice segment for each nonempty
   section using ElevenLabs, then uses FFmpeg to align and concatenate audio
   to the timeline. Estimated narration captions also follow the sections
   instead of playing only at the beginning. These calls can incur **multiple
   TTS charges per clip**; choose script-only for a free review first.
6. **Library → Refine this clip** offers **Regenerate timed narration** from
   the selected segment, optionally with explicit consent for new TTS.

**Install the optional vision model on the Ollama Docker service before using
Video frames + spoken words**:

```bash
cd ~/ClipForge
docker compose --profile ai exec ollama ollama pull moondream
docker compose --profile ai exec ollama ollama list
```

`moondream` is a small (~1.7 GB) image-capable model suited to CPU devices.
The downloader and narrator models run locally; ElevenLabs receives only
narration text when explicitly selected. You can choose **Spoken words only**
without downloading a vision model. Silent footage requires video analysis
rather than speech-only mode, and speech-based highlight ranking still needs
audio. Existing completed clips aren't retroactively changed.

**Slow CPU vision and recovery:** The default Moondream prompt uses the short
single-sentence request successfully verified against a real source frame.
If a model returns blank text, ClipForge logs its finish reason, output-token
count and duration (never the frame or prompt) and tries a nearby frame with
a second short prompt. Each successful observation is checkpointed as text in
the job's private narration directory; a failed or interrupted job can resume
without reanalyzing already-observed windows. Checkpoints are invalidated
when the source file, selected timing, vision model or prompt version changes.
No images are retained. CPU-only Moondream may take **two minutes or more per
sampled frame**, so a 60-second clip with eight narration windows can take
over 15 minutes for its visual pass alone. See the container logs for
`Narration visual evidence ready: window N/M` while waiting; avoid repeatedly
resuming a job that is still processing.

**Timed script reliability:** The language model writes at most two windows
per request using a JSON schema and checks that every returned ID matches the
requested window. A malformed or truncated two-window reply is retried as
individual windows; if that also fails, the job stops without inventing a
script. Valid text drafts are saved separately from frame descriptions so a
resumed job can continue without repeating earlier visual analysis or valid
narration beats. Follow `Timed narration script ready: windows N-M/T` in the
container logs. No source text or raw model output is included in error logs.

**Limitations:** This is a still-frame sampler, not motion recognition or
continuous shot-by-shot video understanding. It can miss quick events or
misdescribe images. Review the timed script against the clip and correct
errors before publishing. Timing for captioned TTS is approximate rather than
provider-exact word alignment. Generation may be slow on CPU-only servers,
especially with several clips and visual frames. Preserve the original
footage and only clip videos you are authorized to use.

## Creator-directed clip discovery and publishing drafts (v0.10)

The **AI editor** now accepts a bounded, explicit instruction such as "Find
funny reactions, unexpected travel moments, or the most useful advice".
Select a **channel niche** to guide local Ollama scoring. The prompt is
stored with the job and included in the AI score checkpoint signature, so
different channel goals don't reuse unrelated highlight scores.

The optional **spoken "clip this" marker** is detected from the recorded
video's Whisper transcript and prioritized when selecting windows.
Markers are not live microphone listening or automatic Twitch stream
capture; they are analyzed after you provide your recording. AI scoring
remains a review-required estimate of interesting moments.

**Channel style presets**: Save up to 12 local presets combining preferred
moment instructions, channel niche, captions, framing, effects, hook setting,
spoken markers, and channel sticker text. This supports separate faceless
channels under one parent media brand without storing social account tokens.

**Channel text sticker**: Add an optional 36-character channel label to
completed clips (a sanitized, burned-in ASS overlay in the social safe zone),
and refine it later through Library's non-destructive editor. Text stickers
are not image logos or an automated B-roll library.

**Publishing copy drafts**: From the Publishing page, click **Draft post
copy from video** on a completed clip. The local Ollama worker uses the
selected transcript and available timed storyboard as evidence to propose
a short title, description, and up to 5 relevant hashtags. Drafts are
clearly labeled *AI suggestions requiring human review*, never submitted
automatically. Existing Publishing forms prefill from a draft and remain
fully editable; your explicit approval is still required before posting.
AI drafts are checkpointed per clip and can be regenerated after editing.
Clips without a transcript or storyboard require source transcription or
a video-grounded script first.

**Roadmap differences from Nexus Clips**: Twitch OAuth-based account imports
and post-stream triggers, true livestream marker capture, image/logo sticker
uploads, automatic B-roll selection and zoom/cut choreography still need
separate engineering and creator authorization. They are not implemented or
claimed here. Local processing is limited by the CPU/GPU capacity available
on your own server, rather than offering any unlimited cloud-processing
promise.

## What it does

- Sources: uploaded MP4, MOV, MKV or WEBM (up to 2 GB), or permitted HTTPS links via best-effort `yt-dlp` adapters for YouTube, Twitch, Kick, Vimeo, Dailymotion and TikTok. **Only use content you own or are authorized to download and edit.** YouTube API policies restrict downloading audiovisual content without authorization.
- **AI highlight selection**: local `faster-whisper` transcribes speech with word timestamps and Ollama scores transcript windows for hooks, useful insights, stories and punchlines. The AI picks from bounded video timestamps, returns titles and reasons, and won't silently masquerade as AI when Ollama is offline. Source length limit: 8 hours; up to 100 clips of 10–120 seconds each.
- **AI-assisted editing**: automatic timed captions (classic white, bold yellow, neon pink); color/effect presets (natural, vibrant pop, cinematic, black-and-white); optional crop-to-fill or full-frame padding at 9:16, 16:9 or 1:1. FFmpeg/libass creates the actual MP4 output. AI ranking and clipping remain imperfect; verify each clip before posting.
- **Creator review**: browse job progress, previews, thumbnails, captions/effects badges, and AI-generated titles, then download MP4 or bulk ZIP.
- **Social publishing**: after reviewing each clip, explicitly authorize transfer to connected YouTube Shorts, Instagram Reels and Facebook Page Reels, or send a draft to the TikTok inbox for final posting. Platforms may restrict access or require developer approval.

## Media download network routing

Some Docker hosts resolve YouTube to both IPv4 and IPv6 but have no usable
IPv6 route. The host can successfully connect with `curl -4`, while a
YouTube import fails with `[Errno 101] Network is unreachable`.

ClipForge now defaults to IPv4 for all yt-dlp media imports using
`source_address="0.0.0.0"` (the Python equivalent of `yt-dlp -4`).
This only affects outbound downloader connections; it does not change
Ubuntu, Docker bridge networking, Tailscale, or Ollama.

Use `CLIPFORGE_DOWNLOAD_IP_FAMILY=ipv4` in your server `.env` (default).
For a different host with functioning IPv6, set the option to `ipv6`.
Set `auto` to use system address-family selection. Change requires a
ClipForge container recreation. Never disable TLS verification or delete
your private YouTube cookie file for a routing issue.

To check connectivity inside the container without downloading any video:

```bash
cd ~/ClipForge
docker compose --profile ai exec -T clipforge python - <<'PY'
import socket
for host in ("www.youtube.com", "www.google.com"):
    addresses = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(addresses[0][4])
    print(host, "IPv4: connected")
PY
```

After deploying this patch, select **Activity → Resume job** on the failed
URL import to reuse any existing media, transcripts, and completed clips.
This doesn't guarantee access when YouTube is blocking a session or no
playable format is available.

## YouTube format availability and player challenges

YouTube may return `Requested format is not available` either because a
video lacks the preferred resolution **or** because yt-dlp could not obtain
playable video streams. ClipForge now prefers sources at 1080p or below, with
fallbacks for other available resolutions. It keeps the 2 GB source limit.

The Docker image now includes official **Deno** (JavaScript runtime) and
`yt-dlp[default]` including **yt-dlp-ejs**. These components are required
by current yt-dlp YouTube extractors for many player challenges. A cookie
file alone does not provide the missing JavaScript runtime. The Docker image
smoke test checks both dependencies.

To inspect formats for an **authorized** YouTube URL inside the container,
without exposing login cookies in logs or modifying the protected cookie file:

```bash
cd ~/ClipForge
docker compose --profile ai exec -T clipforge python - <<'PY'
import yt_dlp
from clipforge.import_auth import youtube_cookie_session

url = "https://www.youtube.com/watch?v=Y7kBmcWKLxg"
with youtube_cookie_session(url) as cookies:
    options = {"listformats": True, "skip_download": True}
    if cookies:
        options["cookiefile"] = str(cookies)
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.extract_info(url, download=False)
PY
```

The format table should include playable video/audio options, not only
storyboard entries (e.g., sb0/sb1). If no playable formats are listed,
repeated format retries will not fix the request. Use an original MP4
provided by the rights holder or downloaded through YouTube Studio when
available. After updating Docker, select **Activity → Resume job**;
previously saved source media, transcripts and clips remain intact.

References: [yt-dlp EJS setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
and [yt-dlp format selection](https://github.com/yt-dlp/yt-dlp#format-selection).

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
# Make the directory writable by your SSH user and readable by ClipForge (GID 10001).
sudo install -d -m 750 -o "$(id -un)" -g 10001 private-auth
# Copy your YouTube-only Netscape cookie export into private-auth/youtube.txt
sudo chgrp 10001 private-auth/youtube.txt
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

## Clip Editor: non-destructive revisions and opening hooks

ClipForge **0.4** adds a per-clip editor in **Library**. Open **Refine this clip**
on any completed video to adjust:

- **Start and length:** precision trim within the original video, 3–120 seconds.
- **Headline:** an optional 3-second, top-of-frame hook overlay, with a
  bounded, sanitized title for short-form social videos.
- **Framing and look:** crop/pad plus natural, vibrant, cinematic and noir.
- **Captions:** burned-in white, yellow or neon captions from cached
  Whisper transcription, or regenerate the transcription only if missing.
- **Commentator:** edit the script and explicitly opt in to a new ElevenLabs
  voiceover. This may incur an API charge and is **off by default**.
  Editing script text alone does not produce spoken audio.

Each edit creates a versioned `clip-001-v01.mp4` or subsequent MP4. The
original file is retained and can be downloaded from **Earlier versions**.
The latest version is what Library previews, Publishing sends when newly
approved, and the ZIP export includes. Existing publication records for
older files are preserved; already-published videos are not changed.
Each clip allows up to 20 revisions, and input timing is constrained to the
source duration.

A revision is queued in the same single worker as job processing to avoid
overloading CPU-only machines. If a container restarts, unpaid local edits
can resume automatically. Interrupted paid TTS edits instead enter a failed
revision state without automatic retry to avoid duplicate charges. The
previous MP4 stays intact if any revision fails.

The Studio can optionally add an AI-selected **Opening headline** to newly
generated clips, and the preference is saved under **Settings**. The hook is
taken from the selected highlight's title; audio-energy clips with no title
do not get an invented headline. Headline overlays and captions use FFmpeg
libass and remain reviewable before publishing.

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

- `POST /api/jobs/{id}/clips/{number}/edit`: queue a bounded non-destructive revision for a completed job, supplying `start`, `duration`, `title`, `effect`, `framing`, `caption_style`, `hook_style`, optional `script` and `voiceover`. Paid TTS requires `voiceover_approved: true`; this is an explicit user consent, not a server secret.
- Existing media endpoints expose current clips and prior revision filenames stored in the job's history. The bulk ZIP contains the latest clips only.


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

1. AI scene segmentation, sentence-aware automatic trims, speaker/face tracking, animated captions and brand presets.
2. Native OAuth account connection, account switching, token refresh UI and per-platform upload status verification.
3. Publishing queues, drafts, approval workflows and optional scheduled posting **with explicit creator authorization**.
4. Durable distributed queues, media retention policies, storage quotas and user authentication.

MIT license; see [LICENSE](LICENSE).
