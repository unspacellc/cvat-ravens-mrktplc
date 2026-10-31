# Runbook — release operations (Phase 6)

Each procedure names the code or test that backs it. Items marked **gap** are not implemented; they are listed in `docs/phases/phase-6.md`.

## What to watch (alerts)
No alerting system is wired yet (**gap**). These are the signals to alert on once one exists; each is already queryable.

| Signal | Where | Alert when |
| --- | --- | --- |
| API up | `GET /health` (checks the DB) | non-200 for 2 min |
| Failed operations | `operation.state = 'failed'` (also `GET /operations/{id}`) | any, older than 15 min |
| Stuck operation | `operation.state in ('running','retrying')` with `lease_until` / `run_after` long past | > 15 min |
| Stale projection | `sync_age_s` on `GET /jobs`; worker reconcile result `{checked, changed, missing, errors}` in the worker log | `sync_age_s` > 600, or `errors` > 0 / `missing` > 0 |
| Webhook intake | `webhook_inbox.error` set / attempts at 8; 401 rate on `/webhooks/cvat` | any inbox row at 8 attempts; sustained 401s (secret mismatch, see rotation) |
| Queue age | `GET /metrics/overview` → `queue_age` | oldest `in_review` or `rework` older than the team's SLA |
| Editor backend down | API answers 503 `editor_unavailable` (tested) | any sustained 503 |
| Capacity | host CPU/memory/disk; import worker CPU saturates during ingestion (measured 460% on 8 cores) | CPU > 85% for 10 min; disk > 80% |

## Retry and dead-letter recovery
- **Failed or stuck operation:** an operation retries itself with backoff (5 s · 2ⁿ, 5 attempts). After that it is `failed` and stays visible. Recovery is `POST /operations/{id}/retry` (creator/admin; only `failed`/`cancelled`) or the batch page's Retry button. Completed steps are skipped; for provisioning, half-created CVAT tasks are removed first so nothing is duplicated (`tests/integration/test_phase5_api.py::test_partial_provisioning_is_visible_and_retry_recovers_without_duplicates`).
- **Crashed worker:** nothing to do. A claimed operation holds a 5-minute lease; when it lapses any worker takes it over (`test_resilience.py::test_crashed_worker_lease_expires_and_another_worker_finishes_without_duplicates`).
- **Send-off failure:** re-run the same request (same Idempotency-Key) or retry the operation; export/catalog failures resume and yield exactly one version (`test_sendoff.py`).
- **Webhook inbox:** rows retry up to 8 times; reconciliation repairs state regardless of webhooks (`docs/runbooks/operations-recovery.md`).
- **Dead letters:** operations that exhaust attempts are the dead letters; there is no separate queue. **Gap:** no automatic paging on them.

## Capacity limits
Declared design point: 5 labelers editing + 4 creators ingesting at once. Measured on the local Docker host only (see `docs/capacity.md` and `docs/phases/evidence/load-design-point-2026-10-30.json`); re-measure on the production CPU host before relying on any number.
- Ingestion is CPU-bound in the CVAT import worker; four concurrent 60 s 720p ingestions finished in 7–11.5 s here. On a smaller host they queue, which is safe (operations wait) but slower.
- Request limits that exist: ≤ 200 clips per batch request, ≤ 200 labels per schema, `limit` ≤ 200 on lists. **Gap:** no per-tenant rate limiting.
- Pre-label fan-out is bounded per batch (one run per batch, `max_inflight` chunks) — see ADR 0008. Spend caps ($300/month, $50/batch) are approved but **not implemented**; they belong to the deferred real-AWS pre-labeling work.

## Retention and deletion
- Published dataset versions, their manifests, approved snapshots and review events are immutable (DB triggers and write-once objects). They are never deleted by the application.
- **Gap:** no tenant offboarding or data-deletion procedure exists. A request to delete a tenant's data needs an owner decision (it conflicts with the immutability guarantee) before it is built.
- Backups follow `docs/runbooks/backup-restore.md`; retention of backup copies is the host's policy.

## Access revocation
- **Remove a member:** `DELETE /members/{user_id}` unassigns their open jobs and revokes their CVAT membership (`test_portal_api.py::test_removing_a_member_unassigns_their_jobs`; `test_auth.py::test_unknown_or_removed_member_denied`). The portal API checks membership on every call, so a still-valid Cognito token stops working immediately.
- **Sign a user out everywhere:** delete their marketplace session (`DELETE /sessions/{id}`); the host also ends a sign-in when refresh is refused (`web/app/host/session.test.ts`).
- **Editor sessions:** CVAT access follows CVAT membership/assignment, so revocation takes effect on the next request. A browser session that predates a freeze is also blocked (`tests/capability/test_write_guard.py::test_preexisting_browser_session_is_blocked`).

## Secret rotation
- **Webhook master secret** (tested: `test_resilience.py::test_webhook_secret_rotation_has_no_gap_and_retires_the_old_secret`):
  1. Set `LABELING_WEBHOOK_PREVIOUS_MASTER_SECRET=<old>` and `LABELING_WEBHOOK_MASTER_SECRET=<new>`; restart the API. Deliveries signed with either secret are accepted.
  2. Run `uv run python scripts/rotate_webhook_secrets.py`; it re-keys every tenant's CVAT webhook (exit 1 if any failed; safe to repeat).
  3. Unset the previous secret and restart. Skipping step 2 makes CVAT's old signature fail with 401, and CVAT does not retry 4xx.
- **CVAT service token** (`CVAT_SERVICE_TOKEN`, currently a CVAT superuser — **gap**: replace with a narrower account): create a new token, deploy it, delete the old token in CVAT. Not rehearsed.
- **Marketplace API key** (`MARKETPLACE_API_KEY`) and **Cognito app client secrets:** rotated by their owners; the host and CVAT read them from environment, so rotation is a redeploy. Not rehearsed.

## Rollback
- **Code/config:** redeploy the previous image tag; the API is stateless.
- **Schema:** migrations have downgrades and a round-trip test (`tests/integration/test_schema.py::test_migration_roundtrip`). Downgrade only after stopping the API and workers; a downgrade past 0004 drops review and delivery tables, so restore from backup instead if any delivery exists.
- **CVAT image:** the guarded image is built from the pinned tag + `cvat/patches/0001-write-guard.patch` (`cvat/build-guard.sh`). To roll back, start the previous guard image tag; the write-freeze state lives in the CVAT DB and is unaffected.
- **Disaster:** `docs/runbooks/backup-restore.md`; last drill 2026-10-30 (`docs/phases/evidence/restore-drill-2026-10-30.json`).

## Upgrading the pinned CVAT version
Only the mechanical first step was rehearsed: the guard patch applies cleanly to a pristine `v2.80.0` tree (13 files, 361 added lines). For a real upgrade: create a new worktree at the target tag, apply the patch (`git apply --check` first), rebuild with `cvat/build-guard.sh`, then run `uv run pytest -m capability`; those tests are the contract check (write guard, webhooks, export shapes, OIDC login). Not rehearsed against any version other than the pin.
