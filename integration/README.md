# CVAT labeling integration (standalone, pre-marketplace)

Implements `../CVAT Integration — ….md`. **Current checkpoint: see `docs/phases/`.** Not yet ready for marketplace incorporation (only Phase 7 may claim that).

## Quick start (Docker Desktop, Python via uv, ffmpeg/ffprobe on PATH)
```bash
git worktree add integration/.cvat-src v2.80.0        # once, from repo root (gitignored)
git -C .cvat-src apply ../cvat/patches/0001-write-guard.patch   # once; verified to apply cleanly to the v2.80.0 tag
cd integration && uv sync
cp deploy/env.example deploy/.env
cvat/build-guard.sh                                    # thin overlay image on cvat/server:v2.80.0
WITH_IDP=1 deploy/stack.sh up                          # WITH_IDP only for the dev OIDC tests
deploy/stack.sh health
docker exec -e DJANGO_SUPERUSER_PASSWORD='Adm1n-local-only!' cvat_server python3 /opt/cvat/manage.py \
  createsuperuser --noinput --username admin --email admin@example.com   # dev-only credentials
make check            # offline: ruff + contract tests
make test-capability  # needs the running stack
```
`Adm1n-local-only!` is a local development credential only; production secrets are injected, never committed.
