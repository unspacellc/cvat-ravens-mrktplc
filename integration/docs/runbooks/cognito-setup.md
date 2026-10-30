# Cognito wiring (no dev pool exists yet; this is untested against real Cognito)

Code paths are exercised with Cognito-shaped, really RS256-signed tokens (`test_cognito_shaped_access_token_accepted`).
Real-pool verification stays an open gate (ADR 0005) until a pool is available.

## Portal API
Set `LABELING_COGNITO_REGION`, `LABELING_COGNITO_USER_POOL_ID`, `LABELING_JWT_CLIENT_IDS` (marketplace app client id).
Accepted: access tokens only (`token_use=access`, `client_id` in the allowlist, valid `iss`/`exp`). ID tokens are rejected.
Tenant and roles never come from claims; seed `labeling_member` (via `PUT /members`) keyed by Cognito `sub`.

## CVAT editor (second Cognito prompt)
Create a second app client in the same pool: authorization-code grant, scopes `openid email profile`, a client secret,
callback URL `https://<cvat host>/api/auth/social/oidc/portal/login/callback/`, sign-out URL = portal. Set
`LABELING_OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET` on cvat_server and add the client id to `LABELING_JWT_CLIENT_IDS` only if
that client's tokens will call the portal API (normally not). CVAT accounts are provisioned by `sub`; unprovisioned
Cognito users cannot sign in to CVAT (`LABELING_OIDC_AUTO_SIGNUP` stays unset).

### Dev wiring (aldai_users pool, local CVAT)
The pool already has a hosted-UI domain (`us2safx5mrv6`). Create the editor client once (needs a human with pool admin rights):
```
aws cognito-idp create-user-pool-client --user-pool-id us-east-2_SaFX5MRv6 --client-name labeling-cvat-editor-dev \
  --generate-secret --allowed-o-auth-flows code --allowed-o-auth-flows-user-pool-client --allowed-o-auth-scopes openid email \
  --callback-urls "http://localhost:8080/api/auth/social/oidc/portal/login/callback/" --supported-identity-providers COGNITO \
  --explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH --prevent-user-existence-errors ENABLED --region us-east-2
```
Put the returned `ClientId` / `ClientSecret` in git-ignored `deploy/.env` as `LABELING_OIDC_CLIENT_ID` / `LABELING_OIDC_CLIENT_SECRET`, plus
`LABELING_OIDC_ISSUER=https://cognito-idp.us-east-2.amazonaws.com/us-east-2_SaFX5MRv6`. Then `WITH_COGNITO=1 WITH_IDP=1 deploy/stack.sh up`
and run `scripts/verify_cvat_signin.py` (portal API on :8000, `seed_poc.py` run, a job assigned to the test user).
Do not reuse the marketplace's own app clients: their callback lists belong to the live app.

## Verify once a pool exists
1. `curl <issuer>/.well-known/jwks.json` reachable from the API host.
2. Sign in as a pool user, call `GET /api/v1/labeling/me` with the access token -> 200 for provisioned `sub`, 403/401 otherwise.
3. Open an editor launch URL; Cognito hosted UI should prompt once and land on the job.
