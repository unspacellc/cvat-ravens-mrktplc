# Phase 3 checkpoint — Review and immutable delivery

Date: 2026-10-28 · Branch `feat/labeling-integration-p2` (continues from `phase-2.md`).

## Gate verdict
Exit criteria, each with the test that proves it (all on real CVAT with the write-guard image):

| Criterion | Evidence |
| --- | --- |
| A rejected job returns with issues | `test_review.py::test_full_cycle_reject_resubmit_approve_with_new_revision`; HTTP version in `test_review_api.py::test_full_journey_over_http` |
| Reapproval targets the new revision | same test: the approved snapshot hash differs from the first submit snapshot; stale `expected_snapshot_sha256` → 409 |
| Simultaneous edit / send-off cannot publish unreviewed content | `test_sendoff.py::test_edit_between_approval_and_send_off_cannot_publish`: hash mismatch under the barrier aborts, job returns to `in_review`, nothing published, editing restored |
| Retry after export / registration failure → exactly one version | `test_export_failure_then_retry…`, `test_catalog_outage_keeps_publishing…`: 1 `dataset_version`, 1 outbox event, publisher acks = 1 |
| A revision batch leaves the original version unchanged | `test_revision_batch_seeds_from_published…`: manifest bytes/hash/revision_set equal, source task still 423 |
| Enforce read/write restrictions during review/approval/publication | annotator's own CVAT token is denied after submit; frozen tasks reject writes (`test_write_guard.py`, `test_sendoff.py`) |

Verdict: **Phase 3 gate met against the fixture publisher and local write-once store.** Not a production-delivery claim.

## What was added
Models + migration 0004 (snapshots/events immutable by trigger, issues, dataset versions), `review.py`, `sendoff.py`,
revision batches, routes (`/jobs/{id}/transitions`, `/jobs/{id}/review`, `/batches/{id}/send-offs`,
`/dataset-versions/{id}`, `/dataset-versions/{id}/revision-batches`, `/reviewers`), worker handler, reconcile hook that
invalidates approvals whose content moved, web `ReviewDialog` + send-off/revision controls (and corrected the Phase 2
status names to the real states). Guard patch: export POST allowed on frozen tasks (ADR 0007).

## Honest gaps
- Object immutability is a local fixture; S3 policy/Object Lock/IAM unbuilt. Registration is the fixture publisher.
- Consumer-format conversion = canonical JSON only; no marketplace consumer schema yet; legacy correction manifests not wired.
- Concurrency is exercised by ordering (edit lands before the barrier), not by parallel threads racing the freeze beyond the
  Phase 0 slow-writer test. Request-layer status PATCH is still not atomic with freeze (ADR 0003 gap): content cannot
  slip through (hash check), but a stage/state change racing the freeze is only detected by reconcile.
- Tracks, project-level labels, quality/consensus write paths not covered. Sampled/multi-reviewer policies not implemented.
- Admin "reopen" has an API but no UI button. No browser (Playwright) tests.
- Time-on-task and per-shape review remain unmeasured (as planned).
