#!/usr/bin/env bash
# Quiesced backup of: CVAT Postgres, CVAT data+keys volumes, integration Postgres, plus a checksummed manifest.
# Usage: deploy/backup.sh [out_root]   (default: ./backups). Prints the backup directory on the last line.
# RPO = interval between runs. CVAT API/workers are stopped for the duration (DBs stay up) so all three stores agree.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"; [ -f "$here/.env" ] && set -a && . "$here/.env" && set +a
proj="${CVAT_PROJECT:-cvat-int}"; root="${1:-$here/../backups}"; ts="$(date -u +%Y%m%dT%H%M%SZ)"; out="$root/$ts"
mkdir -p "$out"
app=(cvat_server cvat_ui cvat_worker_utils cvat_worker_import cvat_worker_export cvat_worker_annotation cvat_worker_webhooks cvat_worker_quality_reports cvat_worker_chunks cvat_worker_consensus)
wait_api() { for _ in $(seq 1 90); do curl -fs "http://${CVAT_HOST:-localhost}:8080/api/server/about" 2>/dev/null | grep -q '"version"' && return 0; sleep 2; done; return 1; }
# Always bring CVAT back, and do not return until the API answers (Traefik 404s/serves the UI while it is down).
trap 'docker start "${app[@]}" >/dev/null 2>&1 || true; wait_api || echo "WARNING: CVAT API not ready after backup" >&2' EXIT
docker stop "${app[@]}" >/dev/null
docker exec cvat_db pg_dump -U root -Fc cvat > "$out/cvat.pgdump"
docker exec labeling_db pg_dump -U labeling -Fc labeling > "$out/labeling.pgdump"
for v in cvat_data cvat_keys; do
  docker run --rm -v "${proj}_${v}:/v:ro" -v "$out:/b" postgres:15-alpine tar czf "/b/$v.tgz" -C /v .
done
digests="$(docker inspect cvat_server --format '{{.Image}}')"
python3 - "$out" "$ts" "$digests" <<'PY'
import hashlib, json, pathlib, sys
out, ts, image = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.name != "manifest.json"}
(out / "manifest.json").write_text(json.dumps({"created_utc": ts, "cvat_server_image": image, "sha256": files}, indent=2))
PY
echo "$out"
