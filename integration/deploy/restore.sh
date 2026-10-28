#!/usr/bin/env bash
# Restore a backup made by backup.sh into the running stack's volumes/DBs. DESTRUCTIVE: replaces current CVAT and
# integration state. Requires explicit confirmation: CONFIRM_RESTORE=yes deploy/restore.sh <backup_dir>
set -euo pipefail
[ "${CONFIRM_RESTORE:-}" = yes ] || { echo "refusing: set CONFIRM_RESTORE=yes (this replaces current state)"; exit 2; }
here="$(cd "$(dirname "$0")" && pwd)"; [ -f "$here/.env" ] && set -a && . "$here/.env" && set +a
proj="${CVAT_PROJECT:-cvat-int}"; dir="$(cd "${1:?backup dir}" && pwd)"
python3 - "$dir" <<'PY'   # verify integrity before touching anything
import hashlib, json, pathlib, sys
d = pathlib.Path(sys.argv[1]); m = json.loads((d / "manifest.json").read_text())
for name, want in m["sha256"].items():
    got = hashlib.sha256((d / name).read_bytes()).hexdigest()
    assert got == want, f"checksum mismatch: {name}"
print("backup verified:", m["created_utc"])
PY
app=(cvat_server cvat_ui cvat_worker_utils cvat_worker_import cvat_worker_export cvat_worker_annotation cvat_worker_webhooks cvat_worker_quality_reports cvat_worker_chunks cvat_worker_consensus)
docker stop "${app[@]}" >/dev/null
recreate() {  # container user db dumpfile
  docker exec "$1" psql -U "$2" -d postgres -qc "DROP DATABASE IF EXISTS \"$3\" WITH (FORCE)"
  docker exec "$1" psql -U "$2" -d postgres -qc "CREATE DATABASE \"$3\" OWNER \"$2\""
  docker exec -i "$1" pg_restore -U "$2" -d "$3" --no-owner < "$4"
}
recreate cvat_db root cvat "$dir/cvat.pgdump"
recreate labeling_db labeling labeling "$dir/labeling.pgdump"
for v in cvat_data cvat_keys; do
  docker run --rm -v "${proj}_${v}:/v" -v "$dir:/b:ro" postgres:15-alpine sh -c "find /v -mindepth 1 -delete && tar xzf /b/$v.tgz -C /v"
done
docker start "${app[@]}" >/dev/null
echo restored
