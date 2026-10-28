# 0006 — Portal authentication, role mapping and editor launch

Status: accepted for Phase 2 · Date: 2026-10-28

## Portal API identity
- Bearer JWT, RS256, verified against the issuer's JWKS (`api/labeling/auth.py`): signature, `exp`, `iss`, `token_use=access`
  and application (`client_id`, else `azp`, else `aud`) must match. `alg=none`, wrong issuer/app and expired tokens are rejected.
- Tenant and roles come only from server-side membership (`labeling_member`, keyed by IdP `sub`), never from claims or
  headers. `X-Labeling-Tenant` merely selects among memberships the user already has; ambiguity returns 409.
- Misses on foreign resources return 404 (no existence oracle); role failures 403; token failures 401.

## CVAT identity linking (patch change)
Phase 0's auto-signup OIDC could not be tied to a portal user safely (matching on email is an account-takeover path).
Now the member endpoint links `SocialAccount(provider, uid=sub)` to a CVAT user whose name is derived from `sub`
(`lb-u-<hash>`), and the allauth social adapter refuses sign-in for any identity not linked that way. Tested: provisioned
user resolves to the provisioned CVAT user; unprovisioned identity cannot sign in or self-register; a different `sub` that
claims a provisioned user's email is not linked; one identity cannot link to two users. Removal revokes membership, ends
live sessions and deactivates the account when no other tenant remains.

## Roles -> CVAT
In CVAT v2.80.0 only **maintainers** see jobs they are not assigned to (`jobs.rego`: supervisor == worker for job
visibility). Therefore: labeler and reviewer -> `worker` (see only jobs assigned to them; reviewer gets review work by
assignment, Phase 3 automates this on submit); admin -> `maintainer`; creator -> `worker`. The earlier plan to map
reviewers to `supervisor` was wrong and is dropped. Org-scoped reads need `X-Organization`; the launch URL adds `?org=<slug>`.

## Editor launch
Server builds `{CVAT_PUBLIC_URL}/api/auth/social/oidc/<provider>/login/?process=login&next=/tasks/<t>/jobs/<j>?org=<slug>`.
Checks: tenant scope (404), assignment (403 unless admin), live CVAT assignee equals the user's CVAT id (409 if the
projection lags). Return hosts stay restricted by the CVAT adapter. No token travels in a URL; the user's own IdP login
creates the CVAT session (second Cognito prompt, ADR 0005).

## Web host (dev only)
BFF pattern as in the marketplace: tokens stay server-side, the browser calls a same-origin proxy that adds the bearer and
tenant, and state-changing calls require a custom header plus SameSite=Lax. The host stores the access token in a signed
cookie for simplicity; the marketplace binding keeps its own server-side token store.
