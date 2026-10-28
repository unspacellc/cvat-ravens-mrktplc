# Lessons

- **Chained shell commands hide failures.** A `sed -i` (BSD, macOS) error aborted an `&&` chain and silently skipped creating `workers/run.py`; only a later test exposed it. Use `python` for in-place edits on macOS and check each created file, or avoid long `&&` chains that mix edits with file creation.
- **Traefik serves HTTP 200 (UI) for `/api` while the CVAT server restarts.** Readiness must check the JSON body, not the status code.
- **Test fixtures must not share mutable state across tests.** A session-scoped DB made leftover queued operations and inbox rows from earlier tests interfere; target operations/rows by id.
- **Don't trust `rowcount` for `INSERT … ON CONFLICT DO NOTHING` with psycopg3**; use `RETURNING`.
- **Stock CVAT compose sets `ALLOWED_HOSTS='*'`**, which defeats allauth's return-URL safety check; restrict explicitly.
- **Run destructive Docker cleanup only with explicit user approval**; the Docker VM disk filled with other projects' images.

## Phase 2
7. Verify a role's real permissions in the pinned version's policy files before mapping to it (supervisor did not see unassigned jobs in v2.80.0; found by a failing e2e, not by docs).
8. Never link external identities to local accounts by email; link by immutable IdP `sub` at provisioning and refuse unlinked sign-ins.
9. FastAPI + `from __future__ import annotations` breaks dependencies built from locally-defined `Annotated` aliases; keep annotations eager in route modules.
