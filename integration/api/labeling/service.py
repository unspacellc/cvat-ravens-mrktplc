"""Application services: batch creation (validated, idempotent) and job assignment."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import operations
from .adapters import ClipSource, MediaCatalog
from .config import Settings
from .cvat_client import CvatClient
from .models import (
    CvatIdentityMapping,
    LabelingBatch,
    LabelingJob,
    LabelingProject,
    LabelingTask,
    LabelSchemaVersion,
    TenantCvatOrg,
)


@dataclass
class Ctx:
    settings: Settings
    cvat: CvatClient
    media: MediaCatalog
    clips: ClipSource


class NotFound(Exception):
    pass


def register_schema(session: Session, tenant_id: str, semver: str, labels: list[dict]) -> LabelSchemaVersion:
    content = {"labels": labels}
    h = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    existing = session.scalar(select(LabelSchemaVersion).where(LabelSchemaVersion.tenant_id == tenant_id, LabelSchemaVersion.content_hash == h))
    if existing:
        return existing
    s = LabelSchemaVersion(tenant_id=tenant_id, semver=semver, content=content, content_hash=h)
    session.add(s)
    session.flush()
    return s


def create_batch(session: Session, ctx: Ctx, *, tenant_id: str, name: str, asset_ids: list[str], schema_version_id: uuid.UUID,
                 idempotency_key: str, segment_size: int | None = None) -> tuple[LabelingBatch, operations.Operation]:
    """Validate against the caller's tenant only, then persist batch/tasks and enqueue provisioning (202 semantics)."""
    segment_size = segment_size or ctx.settings.segment_size
    payload = {"name": name, "assets": asset_ids, "schema": str(schema_version_id), "segment_size": segment_size}
    batch_id = uuid.uuid4()
    op, created = operations.enqueue(session, tenant_id=tenant_id, kind="provision_batch", idempotency_key=idempotency_key,
                                     payload=payload, subject_id=batch_id)
    if not created:
        return session.get(LabelingBatch, op.subject_id), op

    schema = session.scalar(select(LabelSchemaVersion).where(LabelSchemaVersion.tenant_id == tenant_id, LabelSchemaVersion.id == schema_version_id))
    if schema is None:
        raise NotFound("label schema version not found for tenant")
    refs = []
    for a in asset_ids:
        try:
            refs.append(ctx.media.get(tenant_id, a))  # KeyError => asset not in this tenant
        except KeyError:
            raise NotFound(f"media asset {a!r} not found for tenant") from None

    project = session.scalar(select(LabelingProject).where(LabelingProject.tenant_id == tenant_id, LabelingProject.schema_version_id == schema.id,
                                                           LabelingProject.cvat_instance == ctx.settings.cvat_instance))
    if project is None:
        project = LabelingProject(tenant_id=tenant_id, schema_version_id=schema.id, cvat_instance=ctx.settings.cvat_instance)
        session.add(project)
        session.flush()
    batch = LabelingBatch(id=batch_id, tenant_id=tenant_id, project_id=project.id, name=name, media_manifest=refs)
    session.add(batch)
    session.flush()
    for ref in refs:
        session.add(LabelingTask(tenant_id=tenant_id, batch_id=batch.id, source_asset_id=ref["source_asset_id"], clip=ref,
                                 cvat_instance=ctx.settings.cvat_instance, segment_size=segment_size))
    session.flush()
    return batch, op


def assign_job(session: Session, ctx: Ctx, *, tenant_id: str, job_id: uuid.UUID, user_id: str, username: str, email: str = "") -> LabelingJob:
    """Manual assignment: ensure CVAT membership (worker) for the user in the tenant's org, set CVAT assignee, record it."""
    job = session.scalar(select(LabelingJob).where(LabelingJob.tenant_id == tenant_id, LabelingJob.id == job_id))
    if job is None:
        raise NotFound("job not found for tenant")
    org = session.get(TenantCvatOrg, (tenant_id, job.cvat_instance))
    ident = session.scalar(select(CvatIdentityMapping).where(CvatIdentityMapping.tenant_id == tenant_id, CvatIdentityMapping.user_id == user_id,
                                                             CvatIdentityMapping.cvat_instance == job.cvat_instance))
    if ident is None or ident.status != "active":
        m = ctx.cvat.ensure_member(org.cvat_org_slug, username, "worker", email)
        if ident is None:
            ident = CvatIdentityMapping(tenant_id=tenant_id, user_id=user_id, cvat_instance=job.cvat_instance, role="worker")
            session.add(ident)
        ident.cvat_user_id, ident.cvat_username, ident.status = m["user_id"], username, "active"
        session.flush()
    ctx.cvat.set_job_assignee(org.cvat_org_slug, job.cvat_job_id, ident.cvat_user_id)
    job.assignee_user_id = user_id
    job.revision += 1
    session.flush()
    return job
