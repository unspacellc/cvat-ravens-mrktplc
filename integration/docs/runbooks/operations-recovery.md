# Runbook — provisioning and webhook recovery (Phase 1)

| Symptom | Meaning | Action |
| --- | --- | --- |
| `operation.state = retrying` | transient CVAT/network error; bounded backoff (5 s·2^n, max 5 attempts) | none; inspect `operation.error` |
| `operation.state = failed` | attempts exhausted or non-retryable (e.g. corrupt media, frame-count mismatch) | fix cause, then `operations.retry(op)` — completed steps are skipped; failed tasks: `provisioning.cleanup_failed_tasks(batch_id)` first |
| `labeling_task.status = failed` | non-retryable per-task failure; other tasks in the batch are unaffected | as above |
| Duplicate CVAT task suspected | task names are `lb-<labeling_task.id>`; provisioning adopts by name | list tasks in the tenant org and compare; delete extras via `CvatClient.delete_task` |
| `webhook_inbox` rows with `error` | refetch failed (CVAT down) | rows retry up to 8 attempts; reconciliation repairs regardless |
| Stale `synced_at` | webhooks dropped (CVAT never retries 4xx; retries 5xx for ~4 days) | `reconcile.reconcile_tenant`; worker runs it every `LABELING_RECONCILE_INTERVAL_S` (default 300) |
| `cvat_state = missing` | job/task deleted in CVAT out-of-band | observation only; restore from backup or re-provision |
