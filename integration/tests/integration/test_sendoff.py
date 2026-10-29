"""Send-off against real CVAT: guarded, resumable, exactly-once publication."""

import json
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from api.labeling import operations, review, sendoff, service
from api.labeling.adapters import ImmutableObjectError
from api.labeling.cvat_client import CvatError
from api.labeling.models import DatasetVersion, LabelingBatch, LabelingJob, LabelingTask, Operation, Outbox

from .conftest import CVAT_URL
from .review_harness import make_setup

pytestmark = pytest.mark.capability


def _approve_all(st, *, draw=True):
    lab, rev = f"lab-{st.tenant}", f"rev-{st.tenant}"
    with st.sf() as s:
        for jid in st.job_ids:
            service.assign_job(s, st.ctx, tenant_id=st.tenant, job_id=jid, user_id=lab, username=service.cvat_username_for(lab), uid=lab)
        s.commit()
    for i, jid in enumerate(st.job_ids):
        if draw:
            st.draw(i, x=10 + i)
        with st.sf() as s:
            j = s.get(LabelingJob, jid)
            review.transition(s, st.ctx, actor=st.actor(lab, "labeler"), job_id=jid,
                              cmd=review.Command("submit", j.revision, reviewer_id=rev))
            s.commit()
            sha = review.live_review_sha(st.ctx, s, st.tenant, jid)
        with st.sf() as s:
            j = s.get(LabelingJob, jid)
            review.transition(s, st.ctx, actor=st.actor(rev, "reviewer"), job_id=jid,
                              cmd=review.Command("approve", j.revision, attestation="all frames reviewed", expected_snapshot_sha256=sha))
            s.commit()


def _run(st, op_id):
    h = {"send_off": sendoff.send_off(st.ctx)}
    return operations.run_one(st.sf, h, op_id=op_id)


def _request(st, key="so-1"):
    with st.sf() as s:
        dv, op = sendoff.request_send_off(s, st.ctx, tenant_id=st.tenant, batch_id=st.batch_id, idempotency_key=key)
        s.commit()
        return dv.id, op.id


@pytest.fixture
def ready(session_factory, cvat_token, clips):
    t = f"tenant-so-{uuid.uuid4().hex[:8]}"
    st = make_setup(session_factory, cvat_token, clips, t, segment_size=40)  # 2s@30fps => 60 frames => 2 jobs
    _approve_all(st)
    return st


def _counts(st):
    with st.sf() as s:
        return (s.scalar(select(func.count()).select_from(DatasetVersion).where(DatasetVersion.tenant_id == st.tenant)),
                s.scalar(select(func.count()).select_from(Outbox).where(Outbox.tenant_id == st.tenant)))


def test_happy_path_publishes_one_immutable_version(ready):
    st = ready
    dv_id, op_id = _request(st)
    assert _run(st, op_id)
    with st.sf() as s:
        dv = s.get(DatasetVersion, dv_id)
        assert dv.status == "published" and dv.catalog_ack == f"ack-{dv_id}", (s.get(Operation, op_id).state, s.get(Operation, op_id).error, s.get(Operation, op_id).progress.keys())
        manifest = json.loads(st.ctx.store.get(dv.manifest_uri))
        sendoff._validate("dataset_manifest.schema.json", manifest)
        assert {a["format"] for a in manifest["artifacts"]} == {"cvat_video_1.1", "labeling.canonical_annotations.v1"}
        assert len(manifest["review_evidence"]) == len(st.job_ids) and all(e["attestation"] for e in manifest["review_evidence"])
        canon = next(a for a in manifest["artifacts"] if a["format"].startswith("labeling.canonical"))
        doc = json.loads(st.ctx.store.get(canon["uri"]))
        frames = [f["frame"] for f in doc["clips"][0]["frames"]]
        assert len(frames) == len(set(frames)) and doc["clips"][0]["frames"][0]["source_time_s"] is not None  # no duplicated overlap frames
        assert s.get(LabelingBatch, st.batch_id).lifecycle == "sent_off"
        assert {j.portal_state for j in s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == st.tenant))} == {"sent_off"}
        with pytest.raises(ImmutableObjectError):  # published objects cannot be overwritten
            st.ctx.store.put_if_absent(f"datasets/{st.tenant}/{st.batch_id}/{dv_id}/manifest.json", b"tampered")
    assert _counts(st) == (1, 1)
    # source jobs are non-editable server-side, even for a fresh superuser request
    with st.sf() as s:
        job = s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == st.tenant)).first()
        cvat_job = job.cvat_job_id
    r = httpx.Client(base_url=CVAT_URL, headers={"Authorization": f"Token {st.ctx.settings.cvat_service_token}", "X-Organization": st.slug}).patch(
        f"/api/jobs/{cvat_job}", json={"state": "in progress"})
    assert r.status_code == 423
    # replay: same key -> same op/version; the handler is a no-op; a different key cannot create a second version
    assert _request(st) == (dv_id, op_id)
    assert not _run(st, op_id)
    with st.sf() as s, pytest.raises(service.Conflict):
        sendoff.request_send_off(s, st.ctx, tenant_id=st.tenant, batch_id=st.batch_id, idempotency_key="so-2")
    assert _counts(st) == (1, 1)


def test_not_eligible_until_every_job_is_approved(session_factory, cvat_token, clips):
    t = f"tenant-so-{uuid.uuid4().hex[:8]}"
    st = make_setup(session_factory, cvat_token, clips, t)
    with st.sf() as s, pytest.raises(sendoff.NotEligible) as e:
        sendoff.request_send_off(s, st.ctx, tenant_id=t, batch_id=st.batch_id, idempotency_key="k")
    assert any("not approved" in r for r in e.value.reasons)
    assert _counts(st) == (0, 0)


def test_edit_between_approval_and_send_off_cannot_publish(ready):
    st = ready
    st.draw(0, x=77)  # a straggler edit lands after approval, before the barrier
    dv_id, op_id = _request(st)
    assert _run(st, op_id)
    with st.sf() as s:
        op = s.get(Operation, op_id)
        assert op.state == "failed" and "no longer match" in op.progress["aborted"]
        assert s.get(LabelingBatch, st.batch_id).lifecycle == "created"
        assert s.scalar(select(LabelingJob.portal_state).where(LabelingJob.id == st.job_ids[0])) == "in_review"  # approval invalidated
    assert _counts(st) == (0, 0)
    with st.sf() as s:  # barrier lifted: the abort restored editing so the work can continue
        cvat_job = s.get(LabelingJob, st.job_ids[0]).cvat_job_id
    c = httpx.Client(base_url=CVAT_URL, headers={"Authorization": f"Token {st.ctx.settings.cvat_service_token}", "X-Organization": st.slug})
    assert c.patch(f"/api/jobs/{cvat_job}", json={"state": "in progress"}).status_code == 200


def test_catalog_outage_keeps_publishing_then_retry_yields_exactly_one_version(ready):
    st = ready
    st.ctx.publisher.fail_next = 1
    dv_id, op_id = _request(st)
    assert _run(st, op_id)
    with st.sf() as s:
        op = s.get(Operation, op_id)
        assert op.state == "retrying" and op.error["type"] == "ConnectionError"
        assert s.get(LabelingBatch, st.batch_id).lifecycle == "publishing"
        assert s.get(DatasetVersion, dv_id).status == "publishing"
        op.run_after = operations.now() - timedelta(seconds=1)
        s.commit()
    assert _counts(st) == (1, 0)  # a version row exists but nothing is announced yet
    assert _run(st, op_id)
    with st.sf() as s:
        assert s.get(DatasetVersion, dv_id).status == "published"
    assert _counts(st) == (1, 1) and st.ctx.publisher.calls == 2 and len(st.ctx.publisher.acks) == 1


def test_export_failure_then_retry_yields_exactly_one_version(ready, monkeypatch):
    st = ready
    real, calls = st.ctx.cvat.export_task_native, {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:  # the export worker dies on the first attempt, after the write barrier is in place
            raise CvatError("export worker died", 500, True)
        return real(*a, **kw)

    monkeypatch.setattr(st.ctx.cvat, "export_task_native", flaky)
    dv_id, op_id = _request(st)
    assert _run(st, op_id)
    with st.sf() as s:
        op = s.get(Operation, op_id)
        assert op.state == "retrying" and "frozen" in op.progress and "artifacts" not in op.progress
        op.run_after = operations.now() - timedelta(seconds=1)
        s.commit()
    assert _run(st, op_id)
    assert _counts(st) == (1, 1)
    with st.sf() as s:
        assert s.get(DatasetVersion, dv_id).status == "published"


def test_revision_batch_seeds_from_published_and_leaves_original_unchanged(ready):
    from api.labeling import provisioning
    st = ready
    dv_id, op_id = _request(st)
    assert _run(st, op_id)
    with st.sf() as s:
        v1 = s.get(DatasetVersion, dv_id)
        v1_manifest, v1_sha = v1.manifest_uri, v1.manifest_sha256
        v1_bytes = st.ctx.store.get(v1_manifest)
        v1_revset = dict(v1.revision_set)
        batch = s.get(LabelingBatch, st.batch_id)
        batch_asset = batch.media_manifest[0]["source_asset_id"]
    # the original cannot be re-sent, and revising it needs a published version
    with st.sf() as s, pytest.raises(service.NotFound):
        service.create_revision_batch(s, st.ctx, tenant_id=st.tenant, version_id=uuid.uuid4(), idempotency_key="r0")
    with st.sf() as s:
        rb, rop = service.create_revision_batch(s, st.ctx, tenant_id=st.tenant, version_id=dv_id, idempotency_key="rev-1")
        s.commit()
        rb_id, rop_id = rb.id, rop.id
        assert rb.predecessor_version_id == dv_id and rb.id != st.batch_id
    h = {"provision_batch": provisioning.provision_batch(st.ctx)}
    while operations.run_one(st.sf, h, op_id=rop_id):
        pass
    with st.sf() as s:
        assert s.get(LabelingBatch, rb_id).lifecycle == "created"
        new_jobs = list(s.scalars(select(LabelingJob).join(
            LabelingTask, (LabelingJob.tenant_id == LabelingTask.tenant_id) & (LabelingJob.task_id == LabelingTask.id)).where(
            LabelingTask.batch_id == rb_id).order_by(LabelingJob.start_frame)))
        assert len(new_jobs) == len(st.job_ids)
        seeded = [st.ctx.cvat.get_job_annotations(st.slug, j.cvat_job_id) for j in new_jobs]
        assert [len(a["shapes"]) for a in seeded] == [1] * len(new_jobs)  # one rectangle per job, copied from the published snapshots
        assert all(j.portal_state == "created" for j in new_jobs)
    # editing the revision is possible; the published version, its manifest bytes and its frozen source tasks are untouched
    with st.sf() as s:
        cj = s.get(LabelingJob, new_jobs[0].id).cvat_job_id
    st.ctx.cvat.update_job(st.slug, cj, state="in progress")
    with st.sf() as s:
        v1 = s.get(DatasetVersion, dv_id)
        assert (v1.manifest_uri, v1.manifest_sha256, dict(v1.revision_set), v1.status) == (v1_manifest, v1_sha, v1_revset, "published")
        assert st.ctx.store.get(v1_manifest) == v1_bytes
        src_job = s.get(LabelingJob, st.job_ids[0]).cvat_job_id
    with pytest.raises(CvatError, match="423"):
        st.ctx.cvat.update_job(st.slug, src_job, state="in progress")
    assert batch_asset
    # a second revision request with the same key is the same batch; no double-seeding
    with st.sf() as s:
        again, aop = service.create_revision_batch(s, st.ctx, tenant_id=st.tenant, version_id=dv_id, idempotency_key="rev-1")
        assert (again.id, aop.id) == (rb_id, rop_id)
