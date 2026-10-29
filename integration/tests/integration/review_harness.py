"""Shared setup for review/send-off tests: a provisioned one-clip batch with members, on real CVAT."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from api.labeling import fixtures as fx
from api.labeling import operations, provisioning, service
from api.labeling.adapters import Principal
from api.labeling.models import LabelingJob, LabelingMember, LabelingTask, TenantCvatOrg

from .conftest import FIXTURES, build_ctx

LABELS = [{"name": "car", "type": "rectangle", "attributes": []}]


@dataclass
class Setup:
    ctx: service.Ctx
    sf: object
    tenant: str
    batch_id: uuid.UUID
    job_ids: list[uuid.UUID]
    slug: str

    def actor(self, user: str, *roles: str) -> Principal:
        return Principal(user_id=user, tenant_id=self.tenant, roles=frozenset(roles))

    def job(self, s, i=0) -> LabelingJob:
        return s.get(LabelingJob, self.job_ids[i])

    def draw(self, i=0, x=10, label="car"):
        """A 'labeler edit': write one rectangle into the job via CVAT (service credential; user-level denial is tested separately)."""
        with self.sf() as s:
            job = self.job(s, i)
            task = s.get(LabelingTask, job.task_id)
            labels = self.ctx.cvat.task_labels(self.slug, task.cvat_task_id)
            lid = next(k for k, v in labels.items() if v == label)
            self.ctx.cvat.put_job_annotations(self.slug, job.cvat_job_id, {"version": 0, "tags": [], "tracks": [], "shapes": [
                {"type": "rectangle", "frame": job.start_frame, "label_id": lid, "points": [x, x, x + 40, x + 40], "occluded": False,
                 "attributes": [], "source": "manual", "z_order": 0, "rotation": 0, "outside": False}]})


def make_setup(session_factory, cvat_token, clips, tenant: str, *, clip="p1_c", segment_size=40, extra_media=None) -> Setup:
    asset = f"a-{tenant}"
    media = {(tenant, asset): fx.make_media_ref(clips[clip], asset, root=FIXTURES), **(extra_media or {})}
    ctx = build_ctx(cvat_token, media)
    with session_factory() as s:
        schema = service.register_schema(s, tenant, "1.0.0", LABELS)
        batch, op = service.create_batch(s, ctx, tenant_id=tenant, name="rv", asset_ids=[asset], schema_version_id=schema.id,
                                         idempotency_key="rv", segment_size=segment_size)
        s.commit()
        batch_id, op_id = batch.id, op.id
        for sub, roles in (("lab", ["labeler"]), ("lab2", ["labeler"]), ("rev", ["reviewer"]), ("adm", ["admin"])):
            s.add(LabelingMember(tenant_id=tenant, user_id=f"{sub}-{tenant}", roles=roles, email=f"{sub}-{tenant}@example.com"))
        s.commit()
    h = {"provision_batch": provisioning.provision_batch(ctx)}
    while operations.run_one(session_factory, h, op_id=op_id):
        pass
    with session_factory() as s:
        ids = [j.id for j in s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == tenant).order_by(LabelingJob.start_frame))]
        slug = s.get(TenantCvatOrg, (tenant, ctx.settings.cvat_instance)).cvat_org_slug
    return Setup(ctx, session_factory, tenant, batch_id, ids, slug)
