# Phase 2 checkpoint — Standalone portal and access

Date: 2026-10-28 · Branch `feat/labeling-integration-p2` · Builds on `phase-1.md`.

## Gate verdict
| Requirement | Status |
| --- | --- |
| Labeler, reviewer, admin journeys work with real CVAT | **Met at API/session level** (e2e tests); browser UI verified manually through sign-in, board, assignment, my-jobs; **editor hand-off not verified in a browser** (see limitations) |
| Direct URL/API access denied | Met: other assignee 404, other tenant 404, CVAT itself returns 403/404 to the user's own session |
| Tenant switching | Met at API (membership-validated hint, 409 when ambiguous) and in the host UI route; not load/UX tested |
| Expired sessions | Met for portal tokens (401, sign-in-again state). CVAT session lifetime/expiry not tested; removal ends live CVAT sessions (tested) |
| Reassignment | Met: portal and CVAT access move immediately, existing CVAT session loses access without re-login |
| No shared admin session / no stub auth | Met: real RS256 verification (unit tests with generated keys, e2e against the IdP's JWKS); users get per-user CVAT sessions via their own IdP login. The portal's privileged CVAT credential is the service token only |
| **Cognito itself** | **Not met**: no AWS pool available; everything tested against the mock IdP. ADR 0005 gate stays open |

## Evidence
- `uv run pytest tests` -> **89 passed**, `ruff` clean. `web`: `npm run typecheck`, `npm run build`, `npm test` (19 component tests: loading/empty/error/stale/session-expired, launch errors, assignment incl. conflict, paging).
- Real-CVAT e2e: `tests/e2e/test_journeys.py` (5 journeys); API: `tests/integration/test_portal_api.py` (10); auth: `tests/integration/test_auth.py` (8); linking: `tests/capability/test_oidc_login.py` (7).
- Manual browser smoke (puppeteer): sign in via mock IdP as admin -> board shows only own tenant's 3 jobs -> assign to labeler through the dialog (real CVAT call) -> sign in as labeler -> My jobs shows only that job -> Open editor redirected to CVAT's OIDC login.

## What was built
`api/labeling/{auth,routes,main}.py`, `labeling_member` migration 0003, service additions (`set_member`, `remove_member`, `ensure_member_identity`, optimistic assignment), CVAT patch (provisioned-only OIDC linking, deactivate-on-remove), `web/` (feature modules + dev host + BFF), `scripts/seed_dev.py`, ADR 0006.

## Findings and limitations
1. **Dev browser hand-off fails on a hostname**: CVAT redirects to the IdP as `host.docker.internal:8081` (what containers can reach); the host browser cannot resolve it without an `/etc/hosts` entry. Not an issue with Cognito (public issuer). Tests rewrite the host; the browser step beyond the IdP redirect is unverified.
2. **CVAT UI org context**: API reads need `X-Organization`; whether the CVAT UI honors `?org=` on a direct job link is unverified (maintainers/admins depend on it; workers see assigned jobs without it).
3. Reviewer = worker on assigned jobs (CVAT v2.80.0 visibility); no review commands yet (Phase 3).
4. Service token is still a CVAT superuser (carried from Phase 1); least-privilege remains open.
5. Web has component tests but no automated browser (Playwright) suite; the dev host keeps the token in a signed cookie.
6. Concurrency design point (5 labelers + up to 4 creators) is not load-tested (Phase 6).
7. Batch creation UI is Phase 5; the API exists and is tested. Media catalog in the dev API is a fixture.
8. Dev DB needs `alembic upgrade head` (migration 0003) and `scripts/seed_dev.py`; test tenants' webhooks hitting a dev API with a different master secret log 401s (noise).

## Next phase (3) prerequisites
Decide least-privilege CVAT credentials; confirm editor org-context behavior in a real browser; Cognito dev pool test (ADR 0005).
