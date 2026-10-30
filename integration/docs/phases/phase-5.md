# Phase 5 — Creation workflow and theme

Status (2026-10-30): **core gate met on the local stack with manual-only labeling; scoped for the proof of concept.** Real AWS models, the CVAT editor theme patch and in-wizard assignment are deferred (see Gaps).

## What was built
- **Creation wizard** (`web/app/features/labeling/BatchWizard.tsx`, route `/batches/new`): Media → Labels → Details → Preview → Create. Nothing is written before the last step. The Idempotency-Key is fixed per wizard session, so a retry after a failure never creates a second batch. Each step blocks with a specific message; focus moves to the new step heading; the whole path works from the keyboard.
- **Media and labels:** `GET /media` (tenant-scoped catalog), `GET/POST /schemas` (validated, content-hash deduplicated). The dev catalog `LocalDirMediaCatalog` serves `<clip root>/<tenant id>/*.mp4` and cannot cross tenant directories; the marketplace binds its own catalog behind the same `MediaCatalog` protocol (new method `list_assets`).
- **Preview:** `POST /batches/preview` returns clips, tasks and jobs from the real frame map and segment size, with no side effects. A test asserts it equals the jobs provisioning creates.
- **Batch settings persisted:** instructions (with a content-hash version, migration 0006), priority, due date, frames per job, review policy.
- **Provisioning visibility and recovery:** batch detail shows ready/failed clips and jobs created; a failed batch offers Retry (`POST /operations/{id}/retry`). Retry first deletes half-created CVAT tasks, then re-queues, so completed clips are kept and nothing is duplicated.
- **Metrics:** `GET /metrics/overview` (jobs by status, throughput/day, rejection rate, queue age, pre-label duration) and a Metrics page. Time on task is reported as not instrumented; no estimate is shown. Correction metrics stay per pre-label run.
- **Theme:** marketplace token names in light and dark (`.dark` class, system preference, toggle, no flash), status colours as tokens instead of fixed palette colours, visible focus ring, skip link, header with the Labelspace brand and a Back to portal link (`MARKETPLACE_PORTAL_URL`).
- **Manual-only mode:** with no model catalog, the wizard says batches are labeled manually, `/models` is empty, starting a run is refused with a 4xx, and the whole flow below still completes.

## Evidence
- Backend 134 tests pass (9 new): `tests/integration/test_phase5_api.py` (8) and `test_phase5_flow.py` (1). The flow test is the exit flow on real CVAT: multi-clip batch through the wizard endpoints → labeler annotates every job → reviewer requests rework on one → resubmit → approve all → metrics → admin sends off → published version. It runs with no model services.
- Web 96 tests pass (48 before): wizard (6, incl. keyboard-only path and a retry that reuses the Idempotency-Key), batches list, metrics, provisioning recovery, theme toggle, and a WCAG contrast check of every text/background token pair in both themes (4.5:1; focus ring 3:1).
- Browser check against the live stack (real login, dev host, portal API, worker): wizard listed the three clips of the Unspace folder, preview showed 3 tasks / 8 jobs from 270 frames, creating moved to batch detail with live progress, the worker provisioned it and the batch list and metrics showed 8 and 14 jobs. Light and dark screenshots reviewed.

## Gaps and limits
- **Skip link not confirmed visually.** The focus style exists in the built CSS, but the headless test browser has no OS focus, so `:focus` never matched. Verify with a real Tab press.
- **Not tested:** screen reader behaviour, a real keyboard walk of Board/BatchDetail dialogs in a browser, touch layouts.
- **CVAT editor theme patch and "Back to portal" inside CVAT** are not done (spec lists them; they need the CVAT UI build).
- **Assignment** is not a wizard step; it stays on Board/Batch detail.
- **Notification hooks** (`NotificationSink`) are not emitted for batch events yet; no email/Slack delivery.
- **Dev media only:** `LocalDirMediaCatalog` reads local files and fills width/height defaults; the marketplace binding to its per-organization storage is Phase 7 work.
- `contracts/openapi.yaml` `CreateBatch` still describes the Phase 1 marketplace shape (inline media); the live API takes `asset_ids` + `schema_version_id`. Reconcile before Phase 7.
- Seen once and not reproduced: the theme choice showing "System" after a page load in the test browser.
