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
