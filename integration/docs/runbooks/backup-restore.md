# Runbook — backup and restore (Phase 1 baseline)

Covers: CVAT Postgres (`cvat`), CVAT `data` + `keys` volumes, integration Postgres (`labeling`). Not covered: Kvrocks cache/queues
(transient), S3 media (use bucket versioning/replication), Grafana/ClickHouse analytics.

## Backup
`deploy/backup.sh [out_root]` — stops the CVAT API/UI/workers (DBs stay up), `pg_dump -Fc` both DBs, tars both volumes, writes
`manifest.json` with SHA-256 per artifact, restarts CVAT and **waits until the API answers**. ~25 s on the dev dataset.
Schedule it at your RPO; store backups off-host with the same retention as the media they reference.

## Restore (destructive)
```
CONFIRM_RESTORE=yes deploy/restore.sh backups/<timestamp>
```
Verifies checksums first, stops CVAT, recreates both DBs from the dumps, replaces both volumes, restarts CVAT.
Then: `deploy/stack.sh health`; run a reconcile (`reconcile.reconcile_all`) and confirm `missing == 0 && errors == 0`;
spot-check one task's first frame. In-flight operations resume by lease expiry (`operations.claim_next`).

## Evidence
`docs/phases/evidence/restore-drill-2026-10-28.json` (`scripts/restore_drill.py`): deleted task and wiped integration rows were
recovered; frame bytes, job states, annotation counts and CVAT task id identical; API back in ~30 s.
