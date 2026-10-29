# Phase 4 checkpoint — AWS batch pre-labeling

Date: 2026-10-28 · Builds on `phase-3.md`.

## Gate verdict
**Offline/fake gate met. Real-AWS gate NOT met** (no AWS account or endpoint was available; owner confirmed there is no dev
Cognito pool either). The exit criterion "Real AWS evidence and per-batch resource/cost measurements accompany offline tests"
is therefore open, and this phase must not be reported as production-ready.

| Criterion | Evidence |
| --- | --- |
| Pre-labels align with expected CVAT frames/coordinates before editing is enabled | `test_prelabels_align_with_pixels_through_letterbox_offset_and_vfr`: the fake finds the box *in the letterboxed 256×256 pixels*, the pipeline inverts it, and every imported box on a variable-rate clip with a 1.5 s container offset and a 12.5 s source offset matches a content-clock ground truth within 2.5 px; absent frames get no shape |
| Resize/letterbox, nonzero offset, sampled/VFR frames, empty predictions, unknown labels | same test + `test_sampled_frames_only_predict_every_nth_frame` (stride 3), unknown label counted (`dropped_unknown_label`) and never imported |
| Timeout/replay cannot duplicate shapes or erase human edits | `test_replay_never_duplicates…`: duplicate delivery no-op; lost-ledger replay adopts the existing import (count unchanged); a human-edited job is refused (`conflict`), edit preserved |
| Failed and zero-detection runs are distinguishable | `test_zero_detections_failure_and_cancellation_are_distinguishable` (+ transient / malformed-response test); UI states in `PrelabelPanel.test.tsx` |
| Bounded queueing, cancellation, manual fallback | `test_inflight_requests_are_bounded` (≤2 in flight over 20 requests), cancel mid-flight (nothing imported, backend cancel called), `test_manual_fallback…` |
| Annotation blocked until import completes | `test_annotation_is_gated_while_running…`, `prelabel_in_progress` over HTTP |
| Baseline, attestation, correction metrics with limitations | `test_correction_metrics_measure_human_changes…` (edited/deleted/added/unchanged, attestation + revision), `tests/contract/test_metrics.py` |
| Entitlement / pinned model / provider checks | `test_entitlement_provider_and_mapping_are_checked_up_front`, `/models` listing |

## Resource measurements (offline, fixture only)
80 frames, 5 requests of 16 frames: 75,752 input bytes total (~947 B/frame), 0.6 s pipeline wall time with the fake endpoint.
**Do not extrapolate**: the fixture is a black frame with one white box, which PNG-compresses to almost nothing. Real footage
will be one to two orders of magnitude larger per frame. No AWS-side compute, S3 request or inference cost was measured, so the
proposed pre-label limits in `capacity.md` ($300/month, $50/batch) remain unvalidated assumptions.

## Honest gaps
- Real SageMaker async (cold start, autoscaling-to-zero wake-up, payload limits, S3 permissions, retries) is untested; the adapter is
  verified only against fake boto clients (request shape, idempotent submit, 404-vs-other S3 errors).
- Nova (Bedrock) and Rekognition adapters do not exist; the catalog carries `provider` and the API refuses them clearly.
- Frame decoding reads the whole local clip per tick; fine for fixtures, not for large S3-hosted video.
- Correction metrics use rectangles and IoU matching only; no time-on-task; no live-editor instrumentation.
- A bad-looking but well-formed model (wrong boxes inside bounds) is not detected by validation; the human review gate is the control.
- Worker concurrency across multiple processes was exercised via `SKIP LOCKED` claim semantics only, not a multi-worker race test.
