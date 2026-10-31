# Phase 6 — Release qualification

Status (2026-10-30): **gate met for the proof-of-concept scope on the local stack; this is not a production release qualification.** The load numbers come from a laptop running emulated amd64 containers, and nothing here touched real AWS, a production CVAT host, or S3. Open items below have no owner yet; the owner column says "to assign".

## Defects found and fixed during qualification
1. **Editor outage returned a bare 500.** A `CvatError` had no handler, so an unavailable CVAT crashed the request. Now: 503 `editor_unavailable` (retryable) or 502 `editor_error` in the standard error envelope, nothing committed, upstream text not echoed. Test: `test_resilience.py::test_editor_outage_*` (fails without the handler: raw `ConnectError`).
2. **Webhook secret rotation had a gap.** Changing the master secret left CVAT signing with the old one, so deliveries failed with 401 and CVAT never retries a 4xx. Now: `LABELING_WEBHOOK_PREVIOUS_MASTER_SECRET` keeps old deliveries valid during rotation, `provisioning.rotate_webhook_secrets` + `scripts/rotate_webhook_secrets.py` re-key every tenant. Test shows the failure without re-keying and recovery with it.
3. **Test runs polluted the local CVAT and made the suite flaky.** Every test tenant left a webhook behind (973 accumulated). CVAT's webhook worker then retried failed deliveries without end (about 96,000 log lines per 3 minutes), delaying real deliveries; the full suite failed intermittently (a webhook test twice, an end-to-end journey once) while every file passed alone. Fixed by deleting each test's webhooks (`remember_webhooks` in `tests/integration/conftest.py`; `test_migration_roundtrip` wipes the shared DB mid-session, so they are remembered per test). A full run now leaves 2 hooks instead of ~56, and the suite passed.

## Coverage of the spec's bullets
| Spec item | Evidence | Verdict |
| --- | --- | --- |
| Capacity fixture, p95 latency, import/export, queue age, CPU/memory | `docs/capacity.md` (Phase 6 section), `evidence/load-design-point-2026-10-30.json`: 5 labelers + 4 creators, 536 requests, 0 errors; job list p95 19 ms (target 300), editor first load p95 217 ms (3 s), 60 s ingest 6 s alone / 7–11.5 s under load (60 s), export 1.1 s (30 s) | Met on this host only |
| AWS usage vs envelope | Not measured: pre-labeling is deferred | Not done (owner: to assign) |
| Worker crashes | `test_crashed_worker_lease_expires_*`, `test_crash_mid_provisioning_*` | Covered |
| Expired credentials | `test_auth.py::test_rejects_expired_*`; host: `session.test.ts` (refresh refused ends the sign-in) | Covered |
| Unavailable CVAT | `test_editor_outage_*` (503, nothing half-applied, recovers) | Covered |
| Unavailable model endpoints / delayed inference | Phase 4 tests on the offline fake (`test_prelabel.py`, `test_sagemaker_adapter.py`) | Covered offline only; real endpoint not exercised |
| Lost, duplicate, reordered webhooks | `test_webhooks_reconcile.py::test_live_delivery_*` | Covered |
| Concurrent review | `test_concurrent_approvals_produce_exactly_one_approval` (two simultaneous approvals: one 200, one 4xx, one event), `test_review.py::test_independence_roles_and_stale_revision` | Covered |
| Partial exports, catalog downtime | `test_sendoff.py` (export failure, catalog outage → exactly one version) | Covered |
| Tenant isolation, direct API, write barriers, old sessions, worker paths | `test_cross_tenant_access_is_denied_on_every_path`, `test_direct_access_across_assignee_and_tenant_is_not_found`, `test_write_guard.py` (REST, pre-existing browser session, worker path, concurrent writer); barrier also survives a restore | Covered |
| Least-privilege roles | Portal roles enforced server-side (`test_portal_api.py`). The service token is still a CVAT **superuser** | **Gap** (owner: to assign): create a narrower CVAT account for the worker/API |
| Secret rotation | Webhook master secret: tested end to end. CVAT service token, marketplace key, Cognito secrets: procedure written, not rehearsed | Partly met |
| Backup restore, media/snapshot references, recovery point/time | `evidence/restore-drill-2026-10-30.json`: after a total wipe of every labeling table and loss of a CVAT task, all checks matched (frame bytes, jobs, annotations, whole-database row counts, migration revision 0006, published manifest object checksum, freeze barrier). Backup 33.5 s, restore to API 36.3 s on the dev dataset | Met for the dev dataset. The object store is outside the backup (S3 versioning in production); RPO/RTO on production-size data not measured |
| Pinned-version upgrade and patch reapplication | The guard patch applies cleanly to a pristine v2.80.0 (13 files, 361 lines). No upgrade to another version was attempted | **Partly done** (owner: to assign) |
| Alerts, retry/dead-letter recovery, capacity limits, retention/deletion, access revocation, rollback | `docs/runbooks/release-operations.md` | Documented; alert delivery and tenant data deletion are **gaps** |
| Cost/concurrency caps against unbounded model fan-out | Fan-out is bounded per batch (one run per batch, bounded in-flight chunks, ADR 0008). Spend caps ($300/month, $50/batch) approved but not implemented | **Deferred with pre-labeling** |

## Exit criteria
- *No unresolved defect can leak tenant data, corrupt annotations, bypass approval or alter a published version:* none known after the tests above; the isolation, approval-binding and immutability tests all pass in the final full run (138 passed, 1 skipped). This is evidence of absence only for what the tests exercise.
- *Other limitations recorded with an owner and scope constraint:* listed below; owners are not yet assigned.

## Open items (all "owner: to assign")
- **Production-representative measurement:** repeat the load run on the real CPU host with real footage and people-paced editing; add disk high-water mark, export of tasks with many shapes and images, and AWS usage.
- **Real AWS and Cognito:** pre-labeling on a real endpoint (deferred), Cognito-backed CVAT editor sign-in (blocked on creating the app client), marketplace `/sessions` store with the real service key.
- **Narrower CVAT service account**, and rehearsals of the CVAT-token, marketplace-key and Cognito-secret rotations.
- **Alert delivery** (the signals are listed in the runbook; nothing pages anyone), **per-tenant rate limiting**, and a **tenant offboarding/deletion** decision.
- **A real CVAT version upgrade** rehearsal.
- **Load-test realism:** the labelers are scripted at a fixed pace, with one annotation per job.
