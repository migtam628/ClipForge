# ClipForge Guided Studio — design QA

## Reference

Three user-provided Nexus Clips screenshots (13 November 2026): video intake, draggable two-person split framing, and caption style/moment selection. The implementation uses ClipForge's existing dark theme and its real video-processing and library workflows rather than copying the reference branding, content, or subscription screen.

## Implementation coverage

- Four guided steps: source import, framing, style and AI moments, review/create.
- Frame layout choices and individually movable, zoomable source rectangles for vertical two-person clips.
- Portrait preview of a single uploaded local source; URL and batch previews intentionally unavailable before download.
- Caption presets synchronized with actual backend caption styles, plus AI focus, niche, and narration controls.
- Actual FFmpeg split-screen export, saved crop coordinates, and edit/revision support.

## Verification

- Automated API, rendering, page and regression tests: **153 passed** with one environment-only Unix-domain-socket test deselected.
- Ruff lint, JavaScript syntax checks and `git diff --check` passed.
- Attempted browser preview on the provided `terminal.local:4173` endpoint; the browser relay returned `upstream connect error or disconnect/reset before headers`. A separate local FastAPI/Chromium headless attempt served `/studio` successfully but Chromium could not start (`socket() failed: Operation not permitted`). As a result the responsive views, pointer dragging, keyboard adjustments and local-file video preview have **not** been manually verified. User acceptance testing on the running server is required before claiming a pixel/interaction match.

**Final result: blocked for visual QA** (backend and unit/integration tests pass; browser relay unavailable).

## Library three-view redesign and local AI assistant — 16 November 2026

### Design reference and functionality

- Grid: poster-first thumbnail browser with per-clip duration, aspect, status, download and an AI ideas shortcut.
- List: compact searchable, sortable rows with expandable inline player, AI content suggestions and existing clip editor.
- Review: selectable clip rail, full playable video, source metadata, quality inspection, AI content assistant, editing and download/publishing handoff.
- Source processing jobs remain visible and report live progress as a numeric percentage. Status filters, all three views and view preference are preserved across refreshes.
- The AI assistant reuses the existing private local Ollama post-copy worker. It offers video-grounded title/caption/hashtag suggestions, explicitly no automatic narration or publishing, local edits and clipboard copy. It polls only the suggestion panel to avoid interrupting video playback. Regeneration bypasses the cached copy on request.

### Verification status

- `node --check clipforge/static/app.js`, Python `compileall` for edited modules/tests, and `git diff --check`: passed locally.
- The existing remote preview endpoint returned an upstream connection error. A local Playwright/Chromium smoke test was also blocked by host sandbox socket restrictions. Visual/interactive checks for the three screen modes, video playback, AI polling and mobile breakpoints **remain unverified**.
- Run the expanded `tests/test_library_ui.py` and `tests/test_creator_tools.py` in GitHub CI, then review all three views and AI draft generation on an isolated running instance before merging to the deployed main branch.

**Final result: static checks passed; visual acceptance blocked by preview environment.**
