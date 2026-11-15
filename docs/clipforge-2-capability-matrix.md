# ClipForge 2.0 — production capability matrix

Status on 2026-11-15. **Implemented** means present in the codebase, not
independently verified in Mig's production Docker instance. **Partial** means
the experience does not yet meet publication quality without hands-on review.
**Planned** means no feature-complete implementation has shipped.

## A. Import, discovery, and accurate AI

| Capability | Status | Required next acceptance gate |
| --- | --- | --- |
| Import owned files, URLs, batches | Implemented | Recheck each provider against authorized downloads, expired cookies and resumable errors. |
| Extract 1–100 shorts from a long source | Implemented | Evaluate across diverse 15-minute to 8-hour uploads; measure story boundaries, not just successful output. |
| Highlight scoring, niche instructions, spoken “clip this” markers | Partial | Human-rated relevance, beginning/middle/end coverage, and tests with sports, streaming, interviews and travel. |
| Word-aligned captions, caption styles and translations | Partial | Manual caption correction with saved per-word timing; language/terminology test set. |
| Evidence-grounded video narration | Partial | Timestamped shot evidence, voice timing fitting the full clip, no unsupported claims, explicit regenerate-on-edit. |
| Hook, title, hashtag and post-copy suggestions | Partial | Human review, per-channel tone and claims policy, no fabricated scenes. |
| B-roll, templates, automatic zooms, smart cuts | Planned | Preview matches FFmpeg output, source licensing, editable placements and full undo. |

## B. Auto-framing and editing

| Capability | Status | Required next acceptance gate |
| --- | --- | --- |
| Center, pad, blur and face-aware vertical crop | Implemented / partial | Existing face tracking is lightweight Haar-based detection; validate moving subjects, occlusion, fast pans and no-face fallback. |
| Manual two-person stacked crop boxes | Implemented | Sample all crop bounds and verify exported top and bottom subjects remain visible. |
| Six split presets, adjustable 30–70% panel ratio | Implemented in this change | Test local preview vs FFmpeg across source aspect ratios and aspect-correct faces. |
| Synced original and split preview on local files | Implemented in this change | Browser playback, pause and scrub drift checks on multiple codecs and long videos. |
| Frame-accurate URL preview before rendering | Planned | Import to a local staging asset, thumbnails/stream access, secure ownership and cleanup; do not claim a YouTube iframe is synchronized. |
| Active-speaker detection / auto-follow | Planned | Diarized audio + face/mouth correlation, ID continuity, configurable lock/track/switch rules, confidence fallback and correction keyframes. |
| Per-shot split-layout switching | Planned | Timeline layout keyframes, smooth motion and matching render preview. |
| One-source clip cut assembly | Implemented / limited | Current assembled output is bounded to 120 seconds and 12 excerpts. |
| Multi-source **long-form** project timeline | Planned | Arbitrary ordered source clips, transitions, linked/unlinked audio, multi-track overlays, project autosave and reliable 4K/60fps proxies. |
| Audio mix, music, voiceover | Implemented / limited | Waveforms, fades, audio ducking and loudness-normalized exports with human mix preview. |
| Manual title/caption/B-roll/zoom/animation tracks | Planned | Nondestructive drag/trim, snap, ripple, undo/redo, keyframes and frame-accurate render parity. |
| Long-form YouTube export | Planned | 16:9 / 4K presets, chapter markers, segment-based render checkpointing and hours-long media QA. |

## C. Social distribution and operational readiness

| Capability | Status | Required next acceptance gate |
| --- | --- | --- |
| Dedicated Posting Settings screen | Implemented in this change | Create/edit/delete preset without affecting scheduled queue; connection status remains read-only. |
| Persistent per-channel platform/visibility/copy presets | Implemented | Distinguish description templates from final reviewed copy; no secrets in saved presets. |
| YouTube / Instagram / Facebook publishing | Implemented / configuration-dependent | Official API live test with a private/test channel, platform review requirements and duplicate-safe recovery. |
| TikTok | Implemented inbox **draft**, not automatic public post | Complete the TikTok app review/publish flow and label its state correctly. |
| Approved scheduling and reapproval after uncertain transfers | Implemented | Restart and partial-upload simulation, time-zone display audit, duplicate detection. |
| Per-channel publishing calendar, queue, failure retry | Partial | Calendar UI, target-specific validation, status webhooks and permission-appropriate retries. |
| Metadata and platform presets for both shorts and full videos | Partial | Per-platform character/aspect/duration checks, cover/thumbnail editor and scheduled bulk campaigns. |
| Automatic unattended publishing | Not enabled | Opt-in automation, verified account scopes, rights/quality gates, rate limiting, retry policies and a kill switch. |
| Cross-platform analytics and A/B packaging | Partial | Official metric integrations, repeatable experiments, retention reports and performance trend attribution. |

## Delivery sequence and release gates

1. **Framing V2 (this change):** render-parity split ratios, six usable presets,
   source-synchronized local preview and a separate posting-settings area.
2. **Tracking V3:** test-driven face and active-speaker tracking with identity
   persistence, optional diarization, manual keyframe corrections and tracking
   modes: follow, lock, two-speaker split, automatic switch and full scene.
3. **Source staging / browser preview:** stream authorized imported footage
   from the private server, build frame thumbnails and a single synchronized
   timeline preview used by clipping and full-length projects.
4. **Long-form timeline:** persisted multi-source project schema, 3–5 tracks
   (video, B-roll/graphics, original audio, narration and music), cut/move/
   trim, ripple, fades, transitions, waveforms, save/undo and scalable render.
5. **Production-quality AI:** evidence-first commentary aligned per scene and
   checked against voice length; clip ranking and captions reviewed against a
   labeled evaluation corpus, with human overrides and re-render.
6. **Publishing operations:** account management, per-channel templates,
   preview/approval calendar, direct vs draft semantics, scheduling, official
   APIs, status/analytics and optional opt-in automated campaigns.

Release acceptance is an **actual sample video** (not only UI tests) for
each layout and source type, a before/after render comparison, transcript and
script review, and a sandbox/private post to each configured platform. No
publish or production deployment should be reported complete without those
checks and Mig's approval of the generated material.

## Research references

- NexusClips official [product description](https://nexusclips.com/about)
  and [video editor feature list](https://nexusclips.com/video-clips/clips-from-videos)
- NexusClips official [AI clipping overview](https://nexusclips.com/video-clips/ai-to-extract-video-clips)
- [NexusClips vs. OpusClip](https://nexusclips.com/compare/nexusclips-vs-opusclip)

These sources describe the reference service; they are **not** evidence that
ClipForge already implements the corresponding features.
