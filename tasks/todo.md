# Plan: CVAT Integration — Phases 0 & 1

## Context
The spec (`CVAT Integration — ….md`) defines an 8-phase build. Phases 0–1 establish feasibility evidence and a CPU CVAT baseline with a durable portal-side data bridge. Nothing exists yet (`integration/` absent). Decisions made: pin **CVAT v2.80.0, built as a patched image from a git worktree of the tag** (current checkout is a 2.80.1-alpha dev build); first auth spike = **Community OIDC/social login against a mock IdP**, with separate CVAT login as fallback baseline.

Key findings that shape the work:
- Webhooks: only `X-Signature-256: sha256=<hmac>` header, **no delivery-id/event header**; dedupe key must be derived from `event` + resource id + `updated_date`. Events: create/update/delete for task/job/issue/etc.; no task-ingestion-complete event (poll `/api/requests/{rq_id}`). Retries only on 5xx/408; 4xx are dropped, so reconciliation is mandatory. Outbound goes through Smokescreen (needs allowlist for local receiver: `tests/allow_webhooks_receiver.sh`).
- `/api/tasks/{id}/data/meta` has **no per-frame timestamps** for video; Phase 0 frame map must use ffprobe pts (`start_frame + i*step`).
- No existing write-freeze: `jobs.rego` allows annotation writes regardless of stage/state. Guard needs a new rego rule (OPA polls rules from server) and/or DRF permission on annotation write paths (`engine/permissions.py` JobPermission, `dataset_manager/task.py`).
- No add-member REST endpoint; invitations need an email backend (else 500). Tenant provisioning for tests: use Django shell/`createsuperuser` via `docker exec`, or the email overlay `tests/docker-compose.email.yml`.
- Auth: API supports Token, PAT (`Bearer`), session, basic. Org scoping via `X-Organization`.
- Marketplace (`../frontend/unspaced/packages/api`): FastAPI under `/api/v1`, `CurrentUser.customer_id` is tenant, Alembic head `b7e2c4d6f810`, outbox `outbox_events` + `OutboxPublisherService`, `AuditLogService.log_event`, `stored_files` (sha256, s3_key) as media source, `ground_truth_service` manifests keyed by (video_hash, model_type) at `ground-truth/{hash}/{model}/…_groundtruth_manifest_<ts>.json`. No model catalog table. Python ≥3.11 (use 3.12 in integration; local default is 3.14, so pin via uv).
- Tooling present: Docker 29.9 (8 CPU/16 GB), uv, node, pnpm.

## Layout (per spec)
`integration/{contracts,api,workers,cvat,deploy,tests,docs/phases,docs/decisions}`; CVAT worktree at `integration/.cvat-src/` (gitignored) from tag v2.80.0.

## Phase 0 — steps → verify
1. Scaffold `integration/` (uv project, py3.12, ruff, pytest, `.gitignore`, Makefile) → `make check` passes.
2. Version pin: worktree at v2.80.0; record image digests, SDK version, edition/capability matrix in `docs/decisions/0001-version-pin.md` → file lists pinned digests.
3. Capability harness (`tests/capability/`, pytest + cvat-sdk against real stack): org + token provisioning, video task create → poll `/api/requests` → jobs, assign, stage/state transitions (stage/state pairs from spec table), issue create/resolve, annotation export, webhook sign verification → all pass against running v2.80.0.
4. Frame-mapping spike: ffprobe pts on a variable-fps fixture clip with nonzero offset and `frame_filter step`; persist mapping schema → test round-trips source↔clip↔CVAT frame.
5. Auth spike: Community social-account/OIDC with mock IdP (e.g. `navikt/mock-oauth2-server`); if unsupported, record and design short-lived single-use exchange patch → ADR `0002-editor-auth.md` with tested outcome.
6. Write-guard spike: patch in `integration/cvat/patches/` (rego deny for `update/delete/import:annotations` when job/task flagged frozen, plus DRF-path check) → tests: direct REST write blocked, worker path blocked, pre-existing session blocked, concurrent save race behavior documented. If not comprehensively enforceable, record **freeze gate NOT passed** (spec requirement).
7. Contracts: OpenAPI + JSON Schemas (`contracts/`) for routes in spec, `labeling.dataset_version.published.v1`, dataset manifest, media reference, frame map; host-adapter Protocols (`IdentityContext`, `MediaCatalog`, `ModelCatalog`, `DatasetPublisher`, `NotificationSink`) with fixtures; correction-manifest coexistence ADR → schema validation tests pass.
8. Load fixture + capacity envelope doc (clip duration/res, frames/job, users, latency targets, recovery objectives) with measured baseline from local stack.
9. Companion POC reconciliation note; Phase 0 checkpoint `docs/phases/phase-0.md` with evidence and exit-gate verdict.

## Phase 1 — steps → verify
1. `deploy/`: compose override pinning v2.80.0 patched image, env examples, healthcheck/readiness script (`make up`, `make health`) → stack healthy from clean checkout.
2. Integration DB (Postgres, separate from CVAT) + SQLAlchemy/Alembic models for: `cvat_identity_mapping`, `label_schema_version`, `labeling_project/batch/task/job`, `operation`, `webhook_inbox`, `outbox`, tenant_id + FK/unique constraints preventing cross-tenant refs → migration up/down + constraint tests.
3. Provisioning: durable idempotent `operation` per step (org→project→task→data→poll→jobs discovery→assign), resumable after partial failure, cleanup of failed tasks; two tenants, multiple clips/tasks/jobs, stable source/frame refs → tests incl. crash/resume with no duplicate tasks.
4. `POST /labeling/webhooks/cvat`: raw-body HMAC verify, persist inbox, ack, async processor with derived dedupe key, refetch authoritative state → tests: bad sig, duplicate, reordered, dropped.
5. Reconciliation worker (configurable interval, default 5 min; sweep) updating job projections with sync age → drop events then confirm convergence within objective.
6. Tenant isolation: per-tenant CVAT org + scoped tokens; test tenant B cannot read tenant A task/job/media via API → isolation tests.
7. Backup/restore: scripts for CVAT DB/data volume + integration DB + object refs; perform one restore and verify projection matches → documented in runbook.
8. Manual labeling demo: assigned user labels fixture clip, projection updates → scripted e2e + `docs/phases/phase-1.md`.

## Process notes
- On approval, copy this plan to `tasks/todo.md` (per global instructions), keep `tasks/lessons.md`, update graphify only if a graph exists (none now).
- Work happens on a feature branch off `dev` (not on `dev` directly); no commits until asked.
- Heavy docker/test runs executed locally; no AWS calls in Phases 0–1.

## Verification
`make up && make health`, `pytest integration/tests/capability integration/tests/contract`, then Phase 1 integration/e2e suites; each phase doc records command output and exit-gate status honestly (including any unmet gate such as freeze enforcement).


---
## Review (2026-10-28) — Phases 0 & 1 implemented
- Phase 0: done; checkpoint `integration/docs/phases/phase-0.md`. Exit gate met with owner-assigned open items; **freeze acceptance not claimed**.
- Phase 1: done; checkpoint `integration/docs/phases/phase-1.md`. `uv run pytest tests` → 63 passed; ruff clean.
- Plan deviations: Postgres-as-queue instead of SQS (deferred); membership via a CVAT patch endpoint (no REST add-member in v2.80.0); worker uses local fixture media.
- Verify commands: `deploy/stack.sh up && deploy/stack.sh health`, `cd integration && make check && make test-capability`.
