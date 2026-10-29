"""provision_batch operation: org -> webhook -> per-task (create-or-adopt, upload, ingest, frame map, jobs).

Every step commits its own progress, and CVAT-side creates are find-before-create (task names are deterministic:
`lb-<labeling_task.id>`), so a crash or ambiguous timeout at any point resumes without duplicating CVAT tasks.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import org_slug_for
from .cvat_client import CvatError
from .framemap import build_frame_map
from .models import (
    AnnotationSnapshot,
    DatasetVersion,
    LabelingBatch,
    LabelingJob,
    LabelingProject,
    LabelingTask,
    LabelSchemaVersion,
    Operation,
    TenantCvatOrg,
)
from .service import Ctx

log = logging.getLogger(__name__)
WEBHOOK_EVENTS = ["create:job", "update:job", "delete:job", "create:task", "update:task", "delete:task",
                  "create:issue", "update:issue", "delete:issue"]


def ensure_tenant_org(session: Session, ctx: Ctx, tenant_id: str) -> TenantCvatOrg:
    inst = ctx.settings.cvat_instance
    row = session.get(TenantCvatOrg, (tenant_id, inst))
    if row is None:
        org = ctx.cvat.ensure_org(org_slug_for(tenant_id))
        row = TenantCvatOrg(tenant_id=tenant_id, cvat_instance=inst, cvat_org_id=org["id"], cvat_org_slug=org["slug"])
        session.add(row)
        try:
            session.commit()
        except IntegrityError:  # concurrent provisioner won the race
            session.rollback()
            row = session.get(TenantCvatOrg, (tenant_id, inst))
    if row.cvat_webhook_id is None:
        hook = ctx.cvat.ensure_webhook(row.cvat_org_slug, ctx.settings.webhook_public_url,
                                       ctx.settings.tenant_webhook_secret(tenant_id), WEBHOOK_EVENTS)
        row.cvat_webhook_id = hook["id"]
        session.commit()
    return row


def _upsert_jobs(session: Session, ctx: Ctx, org: TenantCvatOrg, task: LabelingTask) -> int:
    n = 0
    for j in ctx.cvat.list_jobs(org.cvat_org_slug, task.cvat_task_id):
        job = session.scalar(select(LabelingJob).where(LabelingJob.cvat_instance == task.cvat_instance, LabelingJob.cvat_job_id == j["id"]))
        if job is None:
            job = LabelingJob(tenant_id=task.tenant_id, task_id=task.id, cvat_instance=task.cvat_instance, cvat_job_id=j["id"],
                              start_frame=j["start_frame"], stop_frame=j["stop_frame"])
            session.add(job)
        job.cvat_stage, job.cvat_state = j["stage"], j["state"]
        job.cvat_updated_date = datetime.fromisoformat(j["updated_date"].replace("Z", "+00:00"))
        job.synced_at = datetime.now(UTC)
        n += 1
    return n


def _provision_task(session: Session, ctx: Ctx, org: TenantCvatOrg, task: LabelingTask, labels: list[dict]) -> None:
    slug, name = org.cvat_org_slug, f"lb-{task.id}"
    if task.cvat_task_id is None:
        existing = ctx.cvat.find_task_by_name(slug, name)  # adopt a task created before a crash / ambiguous timeout
        task.cvat_task_id = (existing or ctx.cvat.create_task(slug, name, labels, task.segment_size))["id"]
        session.commit()
    remote = ctx.cvat.get_task(slug, task.cvat_task_id)
    clip = ctx.clips.local_path(task.clip)
    if remote.get("data") is None:
        ctx.cvat.upload_video(slug, task.cvat_task_id, clip)
    task.status = "ingesting"
    session.commit()
    status = ctx.cvat.wait_ingested(slug, task.cvat_task_id)
    remote = ctx.cvat.get_task(slug, task.cvat_task_id)
    fmap = build_frame_map(clip, clip_start_offset_s=task.clip["clip"]["start_offset_s"])
    if fmap.cvat_frame_count != remote["size"]:  # data-integrity error: never retry into a misaligned task
        raise CvatError(f"frame map has {fmap.cvat_frame_count} frames but CVAT ingested {remote['size']} ({status.get('message')})", None, False)
    task.frame_map = fmap.to_json()
    _upsert_jobs(session, ctx, org, task)
    task.status = "ready"
    session.commit()


def seed_revision(session: Session, ctx: Ctx, op: Operation, batch: LabelingBatch, org: TenantCvatOrg) -> None:
    """Revision batch: copy the *published* annotations (snapshots named by the predecessor's revision_set) into the new
    jobs, remapping label ids by name. Recorded per job in the operation so a retry never overwrites human edits."""
    dv = session.get(DatasetVersion, batch.predecessor_version_id)
    shas = set(dv.revision_set.values())
    snaps = session.scalars(select(AnnotationSnapshot).join(LabelingJob, (LabelingJob.tenant_id == AnnotationSnapshot.tenant_id) & (
        LabelingJob.id == AnnotationSnapshot.job_id)).join(LabelingTask, (LabelingTask.tenant_id == LabelingJob.tenant_id) & (
        LabelingTask.id == LabelingJob.task_id)).where(AnnotationSnapshot.tenant_id == batch.tenant_id,
                                                         LabelingTask.batch_id == dv.batch_id, AnnotationSnapshot.sha256.in_(shas))).all()
    by_asset: dict[str, list[dict]] = {}
    for sn in {sn.sha256: sn for sn in snaps}.values():  # submit and approve snapshots of unchanged work share a hash
        doc = json.loads(ctx.store.get(sn.uri))
        task_asset = session.scalar(select(LabelingTask.source_asset_id).join(LabelingJob, (LabelingJob.tenant_id == LabelingTask.tenant_id) & (
            LabelingJob.task_id == LabelingTask.id)).where(LabelingJob.id == sn.job_id))
        by_asset.setdefault(task_asset, []).append(doc)
    done = set(op.progress.get("seeded_jobs", []))
    for task in session.scalars(select(LabelingTask).where(LabelingTask.batch_id == batch.id)):
        names = {v: k for k, v in ctx.cvat.task_labels(org.cvat_org_slug, task.cvat_task_id).items()}
        for job in session.scalars(select(LabelingJob).where(LabelingJob.task_id == task.id)):
            if str(job.id) in done:
                continue
            data = {"version": 0, "tags": [], "shapes": [], "tracks": []}
            for doc in by_asset.get(task.source_asset_id, []):
                for kind in data:
                    if kind == "version":
                        continue
                    for item in doc["annotations"][kind]:
                        frames = [item["frame"]] if kind != "tracks" else [sh["frame"] for sh in item["shapes"]]
                        if kind == "tracks" or job.start_frame <= item["frame"] <= job.stop_frame:
                            if kind == "tracks" and not all(job.start_frame <= f <= job.stop_frame for f in frames):
                                continue
                            clean = {k: v for k, v in item.items() if k != "id"}
                            clean["label_id"] = names[doc["labels"][str(item["label_id"])]]
                            if kind == "tracks":
                                clean["shapes"] = [{k: v for k, v in sh.items() if k != "id"} for sh in item["shapes"]]
                            data[kind].append(clean)
            ctx.cvat.put_job_annotations(org.cvat_org_slug, job.cvat_job_id, data)
            done.add(str(job.id))
            op.progress = {**op.progress, "seeded_jobs": sorted(done)}
            session.commit()


def provision_batch(ctx: Ctx):
    def handler(session: Session, op: Operation) -> None:
        batch = session.get(LabelingBatch, op.subject_id)
        project = session.get(LabelingProject, batch.project_id)
        schema = session.get(LabelSchemaVersion, project.schema_version_id)
        org = ensure_tenant_org(session, ctx, batch.tenant_id)
        failures: list[CvatError] = []
        for task in session.scalars(select(LabelingTask).where(LabelingTask.batch_id == batch.id).order_by(LabelingTask.created_at, LabelingTask.id)):
            if task.status == "ready":
                continue
            try:
                _provision_task(session, ctx, org, task, schema.content["labels"])
            except CvatError as e:
                session.rollback()
                task = session.get(LabelingTask, task.id)
                task.status = "failed" if not e.retryable else "pending"
                session.commit()
                failures.append(e)
        if failures:
            err = CvatError("; ".join(str(f) for f in failures), None, any(f.retryable for f in failures))
            raise err
        if batch.predecessor_version_id:
            seed_revision(session, ctx, op, batch, org)
        batch.lifecycle = "created"
        session.commit()

    return handler


def cleanup_failed_tasks(session: Session, ctx: Ctx, batch_id) -> int:
    """Explicit recovery: delete CVAT tasks of failed portal tasks and reset them for re-provisioning."""
    batch = session.get(LabelingBatch, batch_id)
    org = session.get(TenantCvatOrg, (batch.tenant_id, ctx.settings.cvat_instance))
    n = 0
    for t in session.scalars(select(LabelingTask).where(LabelingTask.batch_id == batch_id, LabelingTask.status == "failed")):
        if t.cvat_task_id is not None:
            ctx.cvat.delete_task(org.cvat_org_slug, t.cvat_task_id)
        t.cvat_task_id, t.status, t.frame_map = None, "pending", None
        n += 1
    session.commit()
    return n
