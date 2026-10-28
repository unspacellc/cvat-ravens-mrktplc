# Phase 1 checkpoint — CPU baseline and data bridge

Date: 2026-10-28 · Branch `feat/labeling-integration-p0-p1` · **Verdict: exit criteria met on the local dev stack with synthetic clips; not yet qualified for load, real footage or a CPU host (Phase 6).**

## What was built
| Area | Where |
| --- | --- |
| Repeatable stack + health/readiness | `deploy/stack.sh {up,down,health,logs}`, `deploy/docker-compose.{guard,integration,idp}.yml`, `deploy/env.example` |
| Integration DB (Postgres) + migrations | `api/labeling/models.py`, `api/migrations/0001–0002`; composite `(tenant_id, id)` FKs reject cross-tenant references |
| CVAT adapter (pinned 2.80) | `api/labeling/cvat_client.py` — find-before-create, `overlap=0`, ambiguity-aware errors |
| Durable operations | `api/labeling/operations.py` — idempotency keys (+conflict), SKIP LOCKED leases, bounded backoff, manual `retry` |
| Tenant + batch provisioning | `service.py`, `provisioning.py` — org → webhook → per-task create/adopt → upload → ingest → frame map → jobs; failed-task `cleanup_failed_tasks` |
| Webhook inbox | `webhooks.py`, `app.py` — raw-body HMAC with per-tenant secret, derived dedupe key, `RETURNING`-based duplicate detection |
| Projection + reconciliation | `projection.py`, `reconcile.py` — observations only; stale snapshots never roll back; out-of-band deletion recorded |
| Worker loop | `workers/run.py` (`tick`: operations → inbox → reconcile on interval) |
| Backup/restore | `deploy/backup.sh`, `deploy/restore.sh`, `scripts/restore_drill.py`, `docs/runbooks/` |
| CVAT patch additions | member provisioning endpoint (`labeling_guard.views.OrganizationMemberView`) |

## Evidence — `uv run pytest tests` → **63 passed**, `ruff` clean
| Exit criterion | Evidence |
| --- | --- |
| Assigned user labels a clip | `test_label_e2e`: user provisioned+assigned via the service, labels with their own CVAT token (frame read, annotation save) |
| CVAT changes reach the projection within the sync objective | webhook path `test_live_delivery…` (real CVAT → host app → inbox → refetch); reconcile path (receiver down) and `test_worker_tick…`; the 300 s default objective is a setting, **not** measured as wall-clock lag |
| Dropped / duplicate / reordered events converge | `test_live_delivery_duplicate_reordered_and_dropped_events_converge` |
| Second tenant cannot reach first tenant's media/jobs | `test_label_e2e` (other-tenant user and unassigned same-org colleague denied: job, annotations, frame data, annotation write), Phase 0 `test_tenant_isolation`, `test_schema` (DB-level), `test_cross_tenant_media_is_rejected` |
| Failed task creation, cleanup, recovery | `test_non_retryable_failure_is_visible_and_recoverable`: failure visible, other task unaffected, cleanup removes CVAT leftovers, `operations.retry` completes only the repaired task |
| Crash/ambiguous timeout → no duplicate CVAT tasks | `test_crash_mid_provisioning…` (lost response after `create_task` and after `upload_video`) |
| Two isolated tenants, multiple clips/tasks/jobs, stable refs | `test_two_tenants_provision_isolated_orgs_tasks_and_jobs` (disjoint job ranges, frame map + sha256 persisted) |
| Initial state restore | `docs/phases/evidence/restore-drill-2026-10-28.json` — deleted task and wiped integration rows recovered; frame bytes, states, annotations identical; API back in ~30 s |
| No local GPU runtime | none used; only CPU containers |

## Findings and limitations (honest list)
1. **The service token is a CVAT superuser** and CVAT does not enforce `X-Organization` on detail lookups for superusers. Service-side isolation is therefore enforced by the integration (tenant-scoped queries through the mapping tables), not by CVAT. Least-privilege split (provisioner superuser only for membership/freeze + per-org maintainer tokens) is a **Phase 2/6** task.
2. Clips are synthetic `testsrc2` videos, not real footage; local runs are emulated amd64; no concurrency/load numbers (see `capacity.md`).
3. `ClipSource`/`MediaCatalog` are local-fixture bindings. S3-backed media (CVAT cloud storage credentials, presigned `remote_files`) is **not** exercised yet.
4. Provisioning uses the integration DB as the queue. SQS and the transactional outbox *publisher* are not wired (the `outbox_events` table exists, mirroring the marketplace columns).
5. Backup is quiesced (~25 s API downtime), omits Kvrocks (transient) and S3; no schedule; RPO = backup interval.
6. Only the `provision_batch` handler and webhook/reconcile run in the worker; Phase 2 adds the user-facing API, inbox/board, editor launch and the real session flow.
7. Membership is provisioned through a patch endpoint; users created that way have unusable passwords and rely on OIDC (ADR 0002 open item 1: licensing decision). Linking a pre-created user to an IdP login by email (`SOCIALACCOUNT_EMAIL_AUTHENTICATION`) is **not** enabled/tested yet.
8. `labeling_job.portal_state` is untouched by CVAT observations by design; workflow transitions arrive in Phase 2/3.
9. The patch file is named `0001-write-guard.patch` but now also carries the OIDC wiring and the member endpoint; split it before Phase 6's upgrade rehearsal.

## Next-phase (Phase 2) prerequisites
- Decide the licensing question (ADR 0002 #1) and the Cognito-as-OIDC-issuer check.
- Least-privilege CVAT credentials (finding 1).
- Companion POC requirements (open since Phase 0).

## Owner decisions received 2026-10-28
- **Identity:** Cognito only, no SSO; one Cognito `sub` per user -> one CVAT user (ADR 0005). Licensing accepted as direction; second Cognito prompt chosen (option 1).
- **Concurrency design point:** 5 labelers + 2 job creators (best) / 4 job creators (worst) simultaneously (`capacity.md`). Not yet load-tested.
- **Models:** registered models in SageMaker; pre-labeling via Nova (Bedrock) and Rekognition (ADR 0004).
- **Companion POC:** received; diffed in `poc-reconciliation.md`.
- **Cost ceiling:** whole system < $4,000/month (currently ~$2.5–2.8k, ~5 users); keep as low as possible while users grow. Confirmed whole-system; labeling gets the ~$1.2k headroom. Proposed sub-limits in `capacity.md` (pre-label $300/month, $50/batch, 75% alarm, scheduled CVAT host) await confirmation.
