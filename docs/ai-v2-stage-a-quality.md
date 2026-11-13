# ClipForge AI Engine 2.0 — Stage A: source-aware trims and observable quality reports

Stage A is a deliberately conservative first increment toward **finished clips
that do not need manual repairs**. It is not a claim that the generated narration
now understands every movement in a video, or that a video will perform well.

## Selection: speech-boundary preservation

- Existing AI/highlight scoring and creator instructions remain in place.
- After candidate selection, ClipForge inspects Whisper segment boundaries.
- If the selected window begins inside a spoken segment, the start can shift
  backward (up to three seconds) to avoid discarding the beginning of a sentence.
- If the window ends inside a spoken segment, the end can extend forward (up to
  three seconds) to preserve the last words.
- A change is skipped if it overlaps another selected clip or exceeds the
  source boundaries or the 120-second narration-compatible range.
- Unavoidable mid-sentence cuts are explicitly recorded in the metadata for
  later review. Silence and unrelated speech do not automatically move the clip.
- Already saved candidates and rendered clips are preserved, avoiding surprise
  changes to completed jobs and resuming work safely.

## Quality checks after every render or clip revision

ClipForge inspects the actual output with FFprobe:

| Area | Deterministic check |
| --- | --- |
| Container/media | Readable playable video track and positive runtime |
| Video | Expected aspect ratio and renderer output resolution |
| Duration | Within the allowed tolerance of the requested cut |
| Audio | Audio track present if source audio or narrator voice is expected |
| Captions | A requested caption overlay was generated |
| Script structure | Timed narration exists and does not repeat entire beats |
| Narration pace | Rough word budget per timed beat |
| Trim boundaries | Flags when speech may have been cut |
| Clip scoring | Advisory warning for scores below 6 |

Reports appear under **View export quality report** in the Library and Publishing
pages. The report is saved under each clip's `quality_report` in the job
metadata. The renderer and existing publishing endpoints are unchanged.

## Status semantics: deliberately strict

- `technical_issue`: a render-level problem is detected.
- `review_required`: automated technical checks passed, but story quality,
  source-grounded factual accuracy, copyright, and platform rules still require
  verification.
- `publish_ready_verified=false`: intentionally remains false in this stage.

**No clip is auto-published and no generated text is claimed verified.**
A quality-score prediction must not be presented as a guarantee of views.

## Next stages

1. Scene-change detection and word/phrase-aware complete-story extraction,
   evaluated against a benchmark suite of user-owned videos.
2. Better multimodal visual/audio evidence windows (not just single-frame
   observations), with uncertainty and timestamped provenance.
3. Semantic script review with contradiction/hallucination checks and
   repetition removal **before** paid TTS requests.
4. Timed voice placement around original speech and important source audio,
   with loudness/ducking QA.
5. Render-and-inspect automatic repair loops, explicit editorial certification,
   and a publishing gate that distinguishes human-approved and machine-audited
   content.

## Release safeguards

- No changes to hosted credentials, `.env`, OAuth tokens, live publishing,
  existing clips, or Docker volumes.
- No automatic API calls or paid TTS added by the quality audit.
- CI covers segment-boundary edge cases, simulated QA failures, a real FFmpeg
  export, and all pre-existing clip processing tests.
- Deploy only after current Phase 1 server activation is verified and processing
  jobs are idle. Re-run a test clip and visually review the report before
  enabling production default changes.
