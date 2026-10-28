# 0002 — Per-user editor authentication

Status: accepted for Phase 0 spike; **licensing/product decision open** · Date: 2026-10-28

## Finding
CVAT documents social/OIDC/SAML login as an Enterprise feature. In the v2.80.0 **Community** image:
- `django-allauth` 65.18 *with* the `openid_connect` provider ships in the image, but it is not in `INSTALLED_APPS`,
  no provider config is loaded, and no `/api/auth/social/*` routes exist.
- So configuration alone cannot enable OIDC in Community.

## Spike (tested)
A small, separable patch (`cvat/patches/0001-write-guard.patch`, env-gated by `LABELING_OIDC_ISSUER`) enables
allauth's `openid_connect` provider and mounts routes at `/api/auth/social/` (Traefik forwards only `/api`, `/static`,
`/admin`, `/django-rq` to the server — `/accounts/` would hit the UI app).

`tests/capability/test_oidc_login.py` drives a full redirect login against a mock OIDC IdP (dev-only
`deploy/docker-compose.idp.yml`) and proves:
- top-level `GET /api/auth/social/oidc/portal/login/?process=login` → IdP → callback → per-user CVAT session;
- a repeat login maps to the same CVAT user; distinct IdP users get distinct identities;
- **return URLs are restricted**. Upstream allauth treats every `ALLOWED_HOSTS` entry as safe and the stock compose
  sets `ALLOWED_HOSTS='*'`, which made `next=https://evil.example` an open redirect (caught by the test).
  `labeling_guard.adapters.StrictReturnAccountAdapter` limits it to the request host + `LABELING_ALLOWED_RETURN_HOSTS`.

## Decision
Use the patched Community route as the working design; **separate CVAT login remains the Phase 1 baseline**.
No shared admin session and no token in URLs are used.

## Open items (owner: Kevin Stephens)
1. **Licensing/product call**: CVAT lists this capability under Enterprise. The patch relies on MIT-licensed CVAT code
   plus allauth, but confirm this is acceptable versus buying Enterprise SSO before depending on it in production.
2. Not yet tested (Phase 2 gate): logout/revocation, reassignment mid-session, tenant switching, CSRF on the logout path,
   and the portal's single-use launch code if the portal (not the IdP) must initiate sessions.
3. Users are auto-created from IdP claims but **have no organization membership**; the provisioning worker must add
   membership (Phase 1/2). Treat IdP `email` as unverified unless the IdP asserts `email_verified`.
4. Production IdP is Cognito, no SSO: see ADR 0005 (decision and the second-prompt finding).
