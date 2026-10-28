# Phase 0 checkpoint — Contracts and feasibility

Date: 2026-10-28 · Branch `feat/labeling-integration-p0-p1` · **Verdict: exit gate met with open decisions (below). Not an incorporation-ready checkpoint.**

## What was built
- `integration/` scaffold (uv, py3.12, ruff, pytest), pinned CVAT v2.80.0 stack launcher (`deploy/stack.sh`), thin patched image (`cvat/Dockerfile.guard`, `cvat/build-guard.sh`), patch `cvat/patches/0001-write-guard.patch` (verified to apply cleanly to the pristine tag).
- Capability harness `tests/capability` against the real stack; offline `tests/contract`.
- Contracts: `contracts/{schemas,examples,openapi.yaml}`, host adapter protocols + fixtures (`api/labeling/{adapters,fixtures}.py`), frame map (`api/labeling/framemap.py`), legacy bridge (`legacy.py`).
- ADRs 0001–0004, `capacity.md`, `poc-reconciliation.md`.

## Evidence
`uv run pytest tests` → **44 passed** (20 contract + 24 capability against the live stack; ≈40 s).

| Spec Phase-0 item | Evidence |
| --- | --- |
| Pin versions/digests, per-capability editions | ADR 0001 |
| Task from clip, ingestion polling, job assignment, review transitions, issues, export, webhook verification on real CVAT | `test_task_ingestion`, `test_workflow`, `test_webhooks_export` |
| Per-user auth | ADR 0002 + `test_oidc_login` (Community needs a small patch; **licensing call open**) |
| Write guard incl. concurrent saves, old sessions, worker path | ADR 0003 + `test_write_guard` |
| Tenancy | `test_tenant_isolation` (cross-org GET/PATCH/DELETE/list/media/annotations denied) |
| Frame/time mapping, VFR, offset, step | `test_frame_alignment` (pixel-checked against CVAT) + `test_framemap` |
| v1 API/event/dataset schemas, adapters, legacy coexistence | `tests/contract` (20), ADR 0004 |
| Load fixture + envelope | `capacity.md` (one emulated baseline; targets proposed) |

## Findings that change the plan
1. Default video `overlap` is non-zero → overlapping jobs; always send `overlap=0`.
2. CVAT webhooks carry no delivery-id/event header; 4xx are never retried → derived dedupe key + mandatory reconciliation.
3. Stock compose `ALLOWED_HOSTS='*'` makes allauth's return-URL check ineffective (open redirect) → strict adapter in the patch.
4. Traefik serves the UI (HTTP 200) for `/api` while the server restarts → health checks must verify the JSON body.
5. No add-member REST endpoint; invitations need an email backend → membership provisioned via Django in the worker/test harness (decide: management command shipped in the patch vs email backend) — Phase 1.
6. CVAT images are amd64-only; local timings are emulated.

## Open items needing an owner
| # | Item | Owner | Blocks |
| --- | --- | --- | --- |
| 1 | OK to rely on patched Community OIDC vs CVAT Enterprise SSO? | Kevin Stephens | Phase 2 gate |
| 2 | Cost ceiling and concurrency targets (`capacity.md`) | Kevin Stephens | Phase 6 |
| 3 | Companion POC requirements (artifact inaccessible) | Kevin Stephens | Phase 2 start |
| 4 | Model registration source (marketplace has no model catalog table) | Marketplace owner | Phase 4 |
| 5 | Legacy `successes/failures` mapping from reviewed annotations | Marketplace/ML owner | Phase 3/7 |
| 6 | Write-guard gaps (ADR 0003): request-layer non-atomic, project-level labels | Integration | Phase 3 acceptance |

**Freeze acceptance is NOT claimed** (spec: only after comprehensive enforcement across mutation paths — Phase 3).

## Next-phase prerequisites
Phase 1 (below) needs: integration DB, membership provisioning approach, webhook receiver reachable from CVAT (done: Smokescreen allowlist).
