
import pytest
from sqlalchemy import select

from api.labeling import fixtures as fx
from api.labeling import operations, provisioning, service
from api.labeling.cvat_client import CvatError
from api.labeling.models import LabelingJob, LabelingTask, Operation, TenantCvatOrg

from .conftest import FIXTURES, build_ctx

pytestmark = pytest.mark.capability
LABELS = [{"name": "car", "type": "rectangle", "attributes": []}]


def _media(tenant, clips, names):
    return {(tenant, f"{n}-{tenant}"): fx.make_media_ref(clips[n], f"{n}-{tenant}", root=FIXTURES) for n in names}


def _drain(session_factory, ctx, op_ids, limit=20):
    handlers = {"provision_batch": provisioning.provision_batch(ctx)}
    for op_id in op_ids:
      for _ in range(limit):
        if not operations.run_one(session_factory, handlers, op_id=op_id):
            break
      else:
        raise AssertionError("operations did not settle")


def _batch(session_factory, ctx, tenant, assets, key, seg=40):
    with session_factory() as s:
        schema = service.register_schema(s, tenant, "1.0.0", LABELS)
        batch, op = service.create_batch(s, ctx, tenant_id=tenant, name="b", asset_ids=assets, schema_version_id=schema.id,
                                         idempotency_key=key, segment_size=seg)
        s.commit()
        return batch.id, op.id, schema.id


def test_two_tenants_provision_isolated_orgs_tasks_and_jobs(session_factory, cvat_token, clips, tenant_ids):
    ta, tb = tenant_ids
    media = {**_media(ta, clips, ["p1_a", "p1_b"]), **_media(tb, clips, ["p1_c"])}
    ctx = build_ctx(cvat_token, media)
    ba, opa, _ = _batch(session_factory, ctx, ta, [f"p1_a-{ta}", f"p1_b-{ta}"], "k-a")
    bb, opb, _ = _batch(session_factory, ctx, tb, [f"p1_c-{tb}"], "k-b")
    _drain(session_factory, ctx, [opa, opb])

    with session_factory() as s:
        orgs = {o.tenant_id: o for o in s.scalars(select(TenantCvatOrg).where(TenantCvatOrg.tenant_id.in_([ta, tb])))}
        assert orgs[ta].cvat_org_slug != orgs[tb].cvat_org_slug and orgs[ta].cvat_webhook_id and orgs[tb].cvat_webhook_id
        tasks_a = s.scalars(select(LabelingTask).where(LabelingTask.batch_id == ba)).all()
        tasks_b = s.scalars(select(LabelingTask).where(LabelingTask.batch_id == bb)).all()
        assert [t.status for t in tasks_a + tasks_b] == ["ready"] * 3
        jobs_a = s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == ta)).all()
        assert len(jobs_a) == 3 + 3  # 120 frames @30fps and 100 frames @25fps, segment 40 -> 3 jobs each
        for t in tasks_a:  # stable source + frame references, overlap=0 => disjoint ranges
            rng = sorted((j.start_frame, j.stop_frame) for j in jobs_a if j.task_id == t.id)
            assert rng[0][0] == 0 and all(b[0] == a[1] + 1 for a, b in zip(rng, rng[1:], strict=False))
            assert t.frame_map["schema_version"] == 1 and t.clip["sha256"]
        # tenant B's CVAT resources live in B's org only
        assert ctx.cvat.get_task(orgs[tb].cvat_org_slug, tasks_b[0].cvat_task_id)["organization"] == orgs[tb].cvat_org_id
        # FINDING: the service token is a CVAT superuser, and CVAT does not enforce X-Organization on detail lookups for
        # superusers. Tenant isolation for service-side access is therefore enforced by the integration (every query is
        # tenant-scoped through the DB mapping); CVAT's own org isolation protects end-user credentials (Phase 0 test).
        assert ctx.cvat.get_task(orgs[ta].cvat_org_slug, tasks_b[0].cvat_task_id)["organization"] == orgs[tb].cvat_org_id
        # the integration layer never resolves another tenant's jobs/tasks
        assert not s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == ta, LabelingJob.task_id.in_([t.id for t in tasks_b]))).all()
        assert s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == tb)).all() and not {j.tenant_id for j in jobs_a} - {ta}


def test_idempotent_replay_and_key_conflict(session_factory, cvat_token, clips, tenant_ids):
    ta, _ = tenant_ids
    ctx = build_ctx(cvat_token, _media(ta, clips, ["p1_c"]))
    b1, op1, schema = _batch(session_factory, ctx, ta, [f"p1_c-{ta}"], "same-key")
    b2, op2, _ = _batch(session_factory, ctx, ta, [f"p1_c-{ta}"], "same-key")
    assert (b1, op1) == (b2, op2)
    with session_factory() as s, pytest.raises(operations.IdempotencyConflict):
        service.create_batch(s, ctx, tenant_id=ta, name="different", asset_ids=[f"p1_c-{ta}"], schema_version_id=schema, idempotency_key="same-key")


def test_cross_tenant_media_is_rejected(session_factory, cvat_token, clips, tenant_ids):
    ta, tb = tenant_ids
    ctx = build_ctx(cvat_token, _media(tb, clips, ["p1_c"]))  # asset belongs to tenant B only
    with session_factory() as s:
        schema = service.register_schema(s, ta, "1.0.0", LABELS)
        with pytest.raises(service.NotFound):
            service.create_batch(s, ctx, tenant_id=ta, name="x", asset_ids=[f"p1_c-{tb}"], schema_version_id=schema.id, idempotency_key="k")


class CrashAfter:
    """Wraps the CVAT client: performs the real call, then fails once to simulate a lost response / crash."""

    def __init__(self, real, method):
        self.real, self.method, self.armed = real, method, True

    def __getattr__(self, name):
        attr = getattr(self.real, name)
        if name != self.method or not callable(attr):
            return attr

        def wrapper(*a, **k):
            out = attr(*a, **k)
            if self.armed:
                self.armed = False
                raise CvatError(f"simulated lost response after {name}", None, True)
            return out
        return wrapper


@pytest.mark.parametrize("crash_after", ["create_task", "upload_video"])
def test_crash_mid_provisioning_resumes_without_duplicate_cvat_tasks(session_factory, cvat_token, clips, tenant_ids, crash_after):
    ta, _ = tenant_ids
    ctx = build_ctx(cvat_token, _media(ta, clips, ["p1_a"]))
    batch_id, op_id, _ = _batch(session_factory, ctx, ta, [f"p1_a-{ta}"], f"crash-{crash_after}")
    ctx.cvat = CrashAfter(ctx.cvat, crash_after)
    handlers = {"provision_batch": provisioning.provision_batch(ctx)}

    assert operations.run_one(session_factory, handlers, op_id=op_id)
    with session_factory() as s:
        op = s.get(Operation, op_id)
        assert op.state == "retrying" and op.attempts == 1
        op.run_after = operations.now()  # skip backoff
        s.commit()
    assert operations.run_one(session_factory, handlers, op_id=op_id)

    with session_factory() as s:
        assert s.get(Operation, op_id).state == "succeeded"
        task = s.scalars(select(LabelingTask).where(LabelingTask.batch_id == batch_id)).one()
        org = s.get(TenantCvatOrg, (ta, ctx.settings.cvat_instance))
        assert task.status == "ready"
    listing = ctx.cvat._req("GET", "/api/tasks", org=org.cvat_org_slug).json()["results"]
    assert [t["name"] for t in listing].count(f"lb-{task.id}") == 1, "exactly one CVAT task for the portal task"


def test_non_retryable_failure_is_visible_and_recoverable(session_factory, cvat_token, clips, tenant_ids, tmp_path):
    ta, _ = tenant_ids
    bad = FIXTURES / "p1_corrupt.mp4"
    bad.write_bytes(b"not a video at all")
    media = _media(ta, clips, ["p1_c"])
    media[(ta, f"bad-{ta}")] = {**fx.make_media_ref(clips["p1_c"], f"bad-{ta}", root=FIXTURES),
                               "storage": {"bucket": "fixtures", "key": "p1_corrupt.mp4", "version_id": None}}
    ctx = build_ctx(cvat_token, media)
    batch_id, op_id, _ = _batch(session_factory, ctx, ta, [f"p1_c-{ta}", f"bad-{ta}"], "bad-key")
    handlers = {"provision_batch": provisioning.provision_batch(ctx)}
    for _ in range(6):
        with session_factory() as s:
            op = s.get(Operation, op_id)
            op.run_after = operations.now(); s.commit()
        operations.run_one(session_factory, handlers, op_id=op_id)

    with session_factory() as s:
        op = s.get(Operation, op_id)
        tasks = {t.source_asset_id: t for t in s.scalars(select(LabelingTask).where(LabelingTask.batch_id == batch_id))}
        assert tasks[f"p1_c-{ta}"].status == "ready"      # the good clip is not blocked by the bad one
        bad_task = tasks[f"bad-{ta}"]
        assert bad_task.status == "failed"
        assert op.state == "failed" and op.error and "ingestion failed" in op.error["message"]  # visible, never a silent success
        org = s.get(TenantCvatOrg, (ta, ctx.settings.cvat_instance))
        stale_cvat_id, bad_task_id = bad_task.cvat_task_id, bad_task.id
        assert stale_cvat_id is not None  # the half-created CVAT task is what cleanup must remove

        # recovery: cleanup deletes the CVAT leftovers and resets only the failed task
        assert provisioning.cleanup_failed_tasks(s, ctx, batch_id) == 1
        s.refresh(bad_task)
        assert (bad_task.status, bad_task.cvat_task_id, bad_task.frame_map) == ("pending", None, None)
        assert tasks[f"p1_c-{ta}"].status == "ready"
    names = [t["name"] for t in ctx.cvat._req("GET", "/api/tasks", org=org.cvat_org_slug).json()["results"]]
    assert f"lb-{bad_task_id}" not in names

    # once the media is fixed, the same operation is retried and only the repaired task is re-provisioned
    (FIXTURES / "p1_corrupt.mp4").write_bytes((FIXTURES / "p1_c.mp4").read_bytes())
    with session_factory() as s:
        operations.retry(s.get(Operation, op_id))
        s.commit()
    while operations.run_one(session_factory, handlers, op_id=op_id):
        pass
    with session_factory() as s:
        assert s.get(Operation, op_id).state == "succeeded"
        assert {t.status for t in s.scalars(select(LabelingTask).where(LabelingTask.batch_id == batch_id))} == {"ready"}
