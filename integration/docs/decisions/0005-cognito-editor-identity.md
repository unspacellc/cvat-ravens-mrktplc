# 0005 — Cognito as the editor identity provider

Status: **accepted (owner chose option 1, second Cognito prompt, 2026-10-28); not yet tested against Cognito** · Date: 2026-10-28 · Supersedes open items 1 and 4 of 0002

## Inputs from the owner
- Marketplace sign-in is **Cognito only; no SSO**. Each user gets one identity when granted access to labeling.
- CVAT Community stays; Enterprise SSO is not being bought.

## Decision
- A Cognito user pool is itself an OIDC issuer (`https://cognito-idp.<region>.amazonaws.com/<pool_id>`, discovery at
  `/.well-known/openid-configuration`). The Phase 0 allauth `openid_connect` patch is pointed at it via
  `LABELING_OIDC_ISSUER`; no extra IdP, bridge, or per-user OIDC provider is needed.
- "A unique OIDC per user" is satisfied by the Cognito `sub`: allauth stores `SocialAccount(uid=sub)`, so each marketplace user
  maps to exactly one CVAT user. Create **one** dedicated Cognito app client for CVAT (code flow, client secret held
  server-side, callback `https://<cvat-host>/api/auth/social/oidc/<provider>/login/callback/`), not one per user.
- The portal provisions the user's CVAT org membership (CVAT creates the user on first login but with no membership).
- Identity key is `sub`. Do not key on email; require `email_verified` before using it as a hint.

## Finding that needs a decision
The marketplace API authenticates with `USER_PASSWORD_AUTH` through its own login UI
(`packages/api/src/services/auth_service.py`), so users have **no Cognito browser session**. Redirecting to the pool's
authorize endpoint therefore requires a Cognito domain (managed login) and the user would sign in a second time.
Options:
1. **Accept a second Cognito prompt** (simplest; works with the Phase 0 patch; same credentials, not a separate account).
2. **Portal-initiated launch**: portal verifies the user's Cognito access token, then mints a single-use, short-lived
   launch code that CVAT-side code exchanges for a session (extra patch, must be threat-modelled; no tokens in URLs).
3. Use the Cognito managed-login domain for the marketplace sign-in too (cross-cutting change; out of scope here).

**Decided: option 1.** Revisit (2) only if users reject the second prompt.

## Tests required (Phase 2 gate)
Real Cognito issuer in a dev pool; same-`sub` re-login maps to the same CVAT user; deactivated Cognito user cannot
start a new session; access removal in the portal removes CVAT membership and open job assignments; logout/CSRF.
