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


---

# Plan: CVAT Integration — Phase 2 (Standalone portal and access)

## Context
Phases 0–1 are committed on `feat/labeling-integration-p0-p1` (507ca5210). Phase 2 (spec lines 330–336) turns the data bridge into a usable product slice:
"My jobs", review queue shell, board, batch detail, manual assignment, per-user editor launch, with loading/empty/failure/stale states, and **real per-user session integration with server-enforced assignment/tenant checks**. Exit gate: labeler, reviewer and admin journeys work against real CVAT; direct URL/API access, tenant switching, expired sessions and reassignment are tested; **no shared admin session, no stub authentication**.

Owner decisions already in hand: Cognito only (no SSO), one Cognito `sub` -> one CVAT user, second Cognito prompt accepted (ADR 0005); design point 5 labelers + 2–4 job creators; whole system < $4,000/month.

Existing pieces to reuse (all under `integration/`): `api/labeling/{app,service,projection,provisioning,cvat_client,adapters,fixtures,config,models}.py`, `Principal`/`IdentityContext` in `adapters.py`, `service.assign_job` / `create_batch`, CVAT patch OIDC route `/api/auth/social/<provider>/login/` and member endpoint, mock IdP compose (`deploy/docker-compose.idp.yml`), test fixtures in `tests/integration/conftest.py` and `tests/capability/conftest.py`.
Marketplace stack to match (`../frontend/unspaced/packages/web`): React Router 7, React 19, Tailwind 4, Radix, TanStack Query/Table, jest/playwright; BFF pattern with server-side token storage (`auth.session.server.ts`, `api-client.server.ts`). Marketplace API signs users in via Cognito `USER_PASSWORD_AUTH`.

## Design
1. **Auth on the portal API (no stubs).** New `api/labeling/auth.py`: bearer JWT verified against the issuer's JWKS (RS256, `iss`, `token_use=access`, `client_id`/`aud`, `exp`), implemented as a concrete `IdentityContext`. Works for Cognito in prod and the mock IdP in dev/tests (both issue genuinely signed JWTs), so tests exercise real signature/expiry checks. Tenant and roles come from a server-side `labeling_member(tenant_id, user_id, role, status)` table (fixture for the standalone host; the marketplace binding replaces it at Phase 7). Identity key is `sub`; never trust tenant/role claims or request headers. Multi-tenant users select a tenant with `X-Labeling-Tenant`, accepted only if a membership row exists.
2. **Role -> CVAT role:** labeler=worker, reviewer=supervisor, admin=maintainer (assumption; recorded in an ADR). Membership/assignment changes call existing `ensure_member`/`revoke_member`/`set_job_assignee`; removal clears open assignments.
3. **Routes** (`api/labeling/routes/`, marketplace `/api/v1/labeling` prefix and `{error,statusCode,code,details,requestId}` envelope):
   `GET /jobs` (inbox/board; filters state/assignee/batch, cursor pagination, `sync_age_s`, operation errors), `GET /batches`, `GET /batches/{id}`, `POST /batches` (202 + operation id; wraps `create_batch`), `POST /jobs/{id}/assignment` (admin; expected `revision`, audited), `POST /jobs/{id}/editor-launch`, `GET /operations/{id}`, `GET/PUT /members` (admin). Labelers only ever see jobs assigned to them; reviewers/admins are tenant-scoped; every query filters `tenant_id` from the resolved principal.
4. **Editor launch:** checks tenant, assignment (or reviewer/admin role), active CVAT identity and synced assignee, then returns an allowlisted URL to CVAT's OIDC login route with `next=/tasks/{t}/jobs/{j}` (return hosts already restricted by `StrictReturnAccountAdapter`). Per-user CVAT session comes from the user's own Cognito/IdP login; no token in URLs, no shared admin session. Reassignment and revocation take effect in CVAT permissions immediately (assignee removed / membership revoked); portal launch refuses stale assignment.
5. **Web (`integration/web/`):** React Router 7 + Tailwind 4 + Radix + TanStack Query, same major versions as the marketplace, so modules map to `packages/web/app`. Feature modules: `my-jobs`, `board`, `batch-detail`, `assign-dialog`, `editor-launch`, plus shared state components (loading/empty/error/stale with sync age). A small standalone host does real OIDC code-flow login against the mock IdP and keeps tokens server-side (BFF) like the marketplace; it is dev-only. Reviewer view is a filtered job list + launch only (submit/reject/approve is Phase 3).
6. **Out of scope:** review commands/snapshots (Phase 3), pre-labeling (4), creation wizard UI (5), Cognito pool test (needs a real AWS dev pool; ADR 0005 marks it Phase 2 gate — run if credentials/pool are provided, otherwise record as unmet and test against mock IdP only).

## Steps -> verify
1. Process: copy this plan to `tasks/todo.md`; keep feature branch (new branch `feat/labeling-integration-p2` off the P0/P1 commit). -> file exists.
2. `labeling_member` migration + `auth.py` (JWKS verifier, tenant/role resolution) -> unit tests: valid, expired, wrong issuer/audience, bad signature, unknown user, cross-tenant header.
3. Routes + service additions (job/batch queries, assignment endpoint with expected revision, members, operations) -> API tests with two tenants: labeler sees only own jobs, direct job-id access to other tenant/assignee is 404/403, optimistic-conflict 409.
4. Editor launch + CVAT identity lifecycle -> real-CVAT tests: assigned labeler logs in via IdP and reads own job; unassigned labeler denied job/annotations/frames; reassignment moves access; removed member loses access; expired portal token rejected; launch URL host allowlist.
5. Web feature modules + standalone host + states -> vitest component tests for all four states per view, typecheck, lint, build; Playwright smoke through mock IdP -> My jobs -> launch.
6. Journeys (scripted e2e on the real stack): labeler, reviewer, admin; tenant switching; direct URL access -> `tests/e2e/`.
7. Docs: ADR 0006 (portal auth, role mapping, launch), `docs/phases/phase-2.md` with evidence, honest gate verdict, known gaps; update capacity/cost notes only if measured. Commit on the Phase 2 branch only after the user asks to keep history tidy? (User said commit at phase boundaries; commit the Phase 2 work at the end.)

## Verification
`uv run pytest tests` (existing 63 plus new), `ruff`, web `typecheck`/`lint`/`vitest`/`build`/Playwright, then `deploy/stack.sh up` with `WITH_IDP=1` and the e2e journeys. The phase doc records outputs and any unmet gate (e.g. Cognito pool test) rather than claiming it.
