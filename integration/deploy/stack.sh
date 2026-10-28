#!/usr/bin/env bash
# Usage: deploy/stack.sh {up|down|health|logs}
# Runs the pinned CVAT compose from the v2.80.0 worktree (integration/.cvat-src).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
[ -f "$here/.env" ] && set -a && . "$here/.env" && set +a
: "${CVAT_VERSION:=v2.80.0}" "${CVAT_PROJECT:=cvat-int}" "${WEBHOOK_TARGET_ALLOW_ADDRESS:=192.168.65.254}"
src="$here/../.cvat-src"
export CVAT_VERSION
export SMOKESCREEN_OPTS="--allow-address=${WEBHOOK_TARGET_ALLOW_ADDRESS}"
files=(-f "$src/docker-compose.yml" -f "$here/docker-compose.guard.yml" -f "$here/docker-compose.integration.yml")
# WITH_IDP=1 adds the dev-only mock OIDC IdP and OIDC env (never in release packaging).
[ "${WITH_IDP:-0}" = 1 ] && files+=(-f "$here/docker-compose.idp.yml")
dc() { docker compose -p "$CVAT_PROJECT" --project-directory "$src" "${files[@]}" "$@"; }
case "${1:-}" in
  up) dc up -d ;;
  down) dc down ;;
  logs) shift; dc logs "$@" ;;
  health)
    # Traefik serves the UI app (HTTP 200 HTML) for /api while the server is down, so require the JSON body.
    curl -fs "http://${CVAT_HOST:-localhost}:8080/api/server/about" | grep -q '"version"' || { echo "cvat api not ready"; exit 1; }
    bad=$(dc ps --format '{{.Name}} {{.State}}' | awk '$2!="running"{print $1}')
    [ -z "$bad" ] || { echo "not running: $bad"; exit 1; }
    echo "healthy" ;;
  *) echo "usage: $0 {up|down|health|logs}"; exit 2 ;;
esac
