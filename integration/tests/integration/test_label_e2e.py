"""Phase 1 exit criteria with real users: an assigned labeler labels a real clip; CVAT edits reach the projection;
an unassigned colleague and another tenant are denied."""

import uuid

import httpx
import pytest
from sqlalchemy import select

from api.labeling import fixtures as fx
from api.labeling import operations, provisioning, reconcile, service
from api.labeling.models import LabelingJob, LabelingTask, TenantCvatOrg

from .conftest import CVAT_URL, FIXTURES, build_ctx, user_token

pytestmark = pytest.mark.capability
LABELS = [{"name": "car", "type": "rectangle", "attributes": []}]


def _provision(session_factory, ctx, tenant, asset):
    with session_factory() as s:
        schema = service.register_schema(s, tenant, "1.0.0", LABELS)
        _, op = service.create_batch(s, ctx, tenant_id=tenant, name="e2e", asset_ids=[asset], schema_version_id=schema.id,
                                     idempotency_key="e2e", segment_size=40)
        s.commit()
        op_id = op.id
    h = {"provision_batch": provisioning.provision_batch(ctx)}
    while operations.run_one(session_factory, h, op_id=op_id):
        pass


def _api(token, org=None):
    headers = {"Authorization": f"Token {token}"}
    if org:
        headers["X-Organization"] = org
    return httpx.Client(base_url=CVAT_URL, headers=headers, timeout=60)


def test_assigned_user_labels_clip_and_other_users_are_denied(session_factory, cvat_token, clips, tenant_ids):
    ta, tb = tenant_ids
    media = {(ta, f"a-{ta}"): fx.make_media_ref(clips["p1_c"], f"a-{ta}", root=FIXTURES),
             (tb, f"b-{tb}"): fx.make_media_ref(clips["p1_c"], f"b-{tb}", root=FIXTURES)}
    ctx = build_ctx(cvat_token, media)
    _provision(session_factory, ctx, ta, f"a-{ta}")
    _provision(session_factory, ctx, tb, f"b-{tb}")
    u = uuid.uuid4().hex[:6]
    with session_factory() as s:
        jobs_a = s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == ta).order_by(LabelingJob.start_frame)).all()
        jobs_b = s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == tb).order_by(LabelingJob.start_frame)).all()
        org_a = s.get(TenantCvatOrg, (ta, "primary"))
        job_a0, job_b0 = jobs_a[0], jobs_b[0]
        alice, carol, bob = f"alice-{u}", f"carol-{u}", f"bob-{u}"
        service.assign_job(s, ctx, tenant_id=ta, job_id=job_a0.id, user_id="u-alice", username=alice, email=f"{alice}@example.com")
        ctx.cvat.ensure_member(org_a.cvat_org_slug, carol, "worker")        # same org, NOT assigned to job_a0
        service.assign_job(s, ctx, tenant_id=tb, job_id=job_b0.id, user_id="u-bob", username=bob, email=f"{bob}@example.com")
        s.commit()
        a0_cvat, b0_cvat, a0_id = job_a0.cvat_job_id, job_b0.cvat_job_id, job_a0.id
        slug_a = org_a.cvat_org_slug

    t_alice, t_carol, t_bob = user_token(alice), user_token(carol), user_token(bob)

    # --- the assigned labeler works: reads frames and saves an annotation with her own credentials
    al = _api(t_alice, slug_a)
    assert al.get(f"/api/jobs/{a0_cvat}").status_code == 200
    assert al.get(f"/api/jobs/{a0_cvat}/data", params={"type": "frame", "number": 0, "quality": "compressed"}).status_code == 200
    label_id = al.get("/api/labels", params={"job_id": a0_cvat}).json()["results"][0]["id"]
    shape = {"type": "rectangle", "frame": 0, "label_id": label_id, "points": [5, 5, 50, 50], "attributes": [], "occluded": False,
             "outside": False, "z_order": 0, "source": "manual"}
    r = al.patch(f"/api/jobs/{a0_cvat}/annotations", params={"action": "create"}, json={"shapes": [shape], "tags": [], "tracks": []})
    assert r.status_code == 200, r.text

    # --- denied: unassigned colleague (same org) and another tenant's user, on every path
    for client, label in ((_api(t_carol, slug_a), "carol (same org, unassigned)"), (_api(t_bob, slug_a), "bob w/ A's org header"), (_api(t_bob), "bob no org")):
        for url in (f"/api/jobs/{a0_cvat}", f"/api/jobs/{a0_cvat}/annotations", f"/api/jobs/{a0_cvat}/data?type=frame&number=0&quality=compressed"):
            assert client.get(url).status_code in (403, 404), (label, url)
        assert client.patch(f"/api/jobs/{a0_cvat}/annotations", params={"action": "create"},
                            json={"shapes": [shape], "tags": [], "tracks": []}).status_code in (403, 404), label
    assert _api(t_alice, slug_a).get(f"/api/jobs/{b0_cvat}").status_code in (403, 404)  # and tenant A can't read B

    # --- CVAT change reaches the projection (reconcile; webhook path is covered separately)
    stats = reconcile.reconcile_tenant(session_factory, ctx, ta)
    with session_factory() as s:
        job = s.get(LabelingJob, a0_id)
        task = s.get(LabelingTask, job.task_id)
        assert stats["errors"] == 0 and job.assignee_user_id == "u-alice"
        assert job.annotation_revision >= 2, "saving annotations in CVAT must advance the observed annotation revision"
        assert task.frame_map and job.synced_at is not None
