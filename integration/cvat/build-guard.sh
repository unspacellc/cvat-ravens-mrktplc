#!/usr/bin/env bash
# Regenerates the reviewable patch from the worktree and builds the guarded server image.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"; src="$here/../.cvat-src"
git -C "$src" add -N cvat/apps/labeling_guard
git -C "$src" diff > "$here/patches/0001-write-guard.patch"
docker build -f "$here/Dockerfile.guard" -t "${GUARD_IMAGE:-cvat-labeling/server:v2.80.0-guard1}" "$src"
