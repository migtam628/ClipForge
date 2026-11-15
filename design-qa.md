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

## Mobile product polish (15 November 2026)

This pass separates mobile navigation from the desktop rail, adds a five-destination
bottom bar and accessible More dialog, and preserves access to Posting settings,
Analytics, the application Settings page, and the API reference. The More dialog
has a dedicated appearance selector synchronized with the desktop selector.
Studio steps scroll horizontally on narrow screens, the next/back actions stay
accessible above the bottom bar, and the original-footage/9:16 framing previews,
split presets, and timeline editor stack at mobile widths. All three theme modes
have corresponding mobile overrides. UI copy and panels were made more compact.

No media job, backend API, publisher credential, or Docker runtime was changed.
The server should **not** be restarted or redeployed while a job is active.

### Mobile acceptance checklist — on the deployed UI after the job finishes

- [ ] At 320px, 375px, 390px, 768px and desktop width, no page-wide horizontal overflow.
- [ ] Studio, Library, Publishing, Activity tabs navigate and highlight correctly.
- [ ] More opens; Posting settings, Analytics, Settings and API docs are reachable.
- [ ] More closes via X, backdrop, and Escape; focus returns to More.
- [ ] Switching theme in More updates every page without resetting job state.
- [ ] Studio stepper scrolls sideways; Back/Continue remain tappable above the bottom bar.
- [ ] Local file framing crop boxes can be dragged with touch; portrait preview matches.
- [ ] Split preset buttons, slider controls, and final review work at 390px.
- [ ] Library preview, clip editor/timeline, publish/schedule form, and settings fields work on a phone.
- [ ] Existing queued/rendering jobs remain available after later, approved deployment.

Validation in this development worktree: JavaScript syntax and standalone static
mobile shell tests pass. Chromium cannot start in this execution environment
(`socket() failed: Operation not permitted`), and its installed Python runtime
lacks project dev dependencies; **browser interaction and full integration QA remain unverified**.
