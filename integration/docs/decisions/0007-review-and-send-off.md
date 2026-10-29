# 0007 — Review evidence and guarded send-off

Status: accepted · Date: 2026-10-28

## Decisions
- **Portal decides, CVAT enforces.** `portal_state` ∈ created → annotating → in_review ⇄ rework → approved → sent_off.
  While in review the *reviewer* is the CVAT assignee (the annotator's own session loses read/write — tested with the
  annotator's real CVAT token); rejection/reopen hands the job back; approval sets `acceptance/completed`.
- **Approval is bound to content, not time.** Evidence is `sha256` of a canonical snapshot of the job's annotations
  (`labeling.job_annotations.v1`: labels by name, shapes/tags/tracks sorted, CVAT's per-save `version` excluded). The
  reviewer approves the hash they were shown (`expected_snapshot_sha256`); a mismatch is a 409. The portal's own CVAT
  writes (`apply_own_write`) adopt the new `updated_date` without bumping `annotation_revision`, so portal transitions
  are never mistaken for annotator edits, and any real edit after approval is caught by hash (`verify_approval`).
- **Independent review by default.** Reviewer ≠ annotator of record; only an admin may override, with a reason, and the
  override is stored in the event. Every ordinary job needs its own approval and attestation (core policy).
- **Evidence is append-only in the database** (`annotation_snapshot`, `review_event` triggers reject UPDATE/DELETE).
  Snapshot bytes go to a write-once object store (`ObjectStore.put_if_absent`).
- **Frame issues are portal-owned** (they gate approval); they are mirrored into CVAT best-effort.
- **Send-off** is one resumable `send_off` operation; each step is recorded in `Operation.progress` and every external
  effect is find-before-create. Order: eligibility → freeze all batch tasks (waits for in-flight writers) → re-verify
  every approved hash against live annotations on the frozen tasks (mismatch ⇒ lift the barrier, drop the unpublished
  version row, return the job to review, fail non-retryably) → native + canonical exports as write-once objects →
  schema-validated manifest → hash re-verification → idempotent registration → one transaction marking the version
  published, jobs/batch `sent_off` and writing the outbox event (event id is deterministic from the version id).
- **One version per batch.** `dataset_version` is unique on (tenant, batch); its id is allocated at request time so
  retries reuse it. Later edits start a **revision batch**: new CVAT tasks seeded from the predecessor's *published*
  snapshots (label ids remapped by name; per-job seeding recorded so a retry never overwrites human edits). The
  published version, its objects and its frozen source tasks are never touched.
- **Guard patch change:** `POST /api/(tasks|jobs)/{id}/dataset/export` is a read-only POST and is allowed on frozen
  tasks (otherwise send-off could not export). Everything else non-safe stays 423.

## Consequences / known limits
- `ObjectStore` is a local write-once fixture. Production needs an S3 bucket policy denying overwrite/delete to the
  application role (and a decision on Object Lock); not built or tested here.
- The canonical export is the only "consumer format". The marketplace consumer schema and the legacy correction-manifest
  adapter are not wired (Phase 7 rehearsal); `contracts/` and `legacy.py` hold the Phase 0 mapping.
- The transitions route relies on `expected_revision` rather than an `Idempotency-Key` (the OpenAPI draft lists both).
- Track-based annotations are snapshotted and exported but not exercised by tests (rectangles only).
