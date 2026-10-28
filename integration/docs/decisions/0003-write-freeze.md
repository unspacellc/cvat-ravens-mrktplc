# 0003 — Server-side write freeze (send-off)

Status: **design viable and tested; acceptance NOT claimed** (completes in Phase 3) · Date: 2026-10-28

## Finding
Upstream CVAT has no freeze: `jobs.rego` permits annotation writes regardless of stage/state, and `completed` is only
workflow state. Read-only PATs restrict a *credential*, not a resource.

## Design (patch `cvat/patches/0001-write-guard.patch`, app `cvat/apps/labeling_guard`)
- `TaskFreeze` row = task is frozen. Superuser-only `PUT/DELETE /api/labeling-guard/tasks/{id}/freeze` (unfreeze is the
  explicit abort path, allowed only before publication).
- **Atomic layer**: all 8 `@transaction.atomic` annotation writers in `dataset_manager/task.py` (put/patch/delete job & task
  data, import task/job annotations) call `assert_writable`, which takes a *shared* transaction advisory lock on the task
  id then checks the row. `freeze()` takes the *exclusive* lock first, so it **waits for in-flight writers to commit**
  and every later writer is rejected. This also covers RQ import workers and any SDK/auto-annotation client.
- **Request layer** (`FrozenTaskMiddleware`, before auth/views, credential-agnostic): non-safe methods on
  `/api/tasks/{id}/…`, `/api/jobs/{id}/…`, `/api/labels/{id}` return **423** — job stage/state/assignee (reopen), task
  edit/delete, data upload/meta, label (schema) edits. Reads stay allowed so send-off can export.

## Evidence (`tests/capability/test_write_guard.py`, passing)
REST annotation PUT/PATCH/DELETE on job and task → 423; reopen, task delete, label edit → 423; reads → 200; unfreeze
restores writes; a browser session established **before** the freeze is blocked after it; non-superusers cannot
freeze; in-container worker-path call (`patch_job_data`/`put_task_data`) raises `TaskFrozenError`; the freezer waited
≈3.0 s for a deliberately slow in-flight writer, which committed before the freeze took effect.

## Known gaps (must close or explicitly scope before Phase 3 acceptance)
- Request-layer check is **not** atomic with the mutation (annotation writes *are*). A reopen/PATCH racing the freeze
  could slip through; send-off step 2 (reconcile revisions after the barrier) must catch it.
- Project-level labels (`label.task_id` null), project edits and tasks moved between projects are not covered.
- Quality-report / consensus / lambda write paths were not individually exercised (they call the same `dataset_manager`
  functions, but this is unverified).
- Direct database access bypasses everything; operationally forbidden by the plan.
- Patch rebase risk on CVAT upgrades (4 touched upstream files; Phase 6 rehearses reapplication).
