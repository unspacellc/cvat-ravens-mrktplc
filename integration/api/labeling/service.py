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
from .config import Settings, cvat_role_for, cvat_username_for
from .cvat_client import CvatClient
from .models import (
    CvatIdentityMapping,
    LabelingBatch,
    LabelingJob,
    LabelingMember,
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


class Conflict(Exception):
    pass


class Invalid(Exception):
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


def _ensure_identity(session: Session, ctx: Ctx, org: TenantCvatOrg, tenant_id: str, user_id: str, username: str, email: str,
                     uid: str | None, role: str) -> CvatIdentityMapping:
    ident = session.scalar(select(CvatIdentityMapping).where(CvatIdentityMapping.tenant_id == tenant_id, CvatIdentityMapping.user_id == user_id,
                                                             CvatIdentityMapping.cvat_instance == org.cvat_instance))
    if ident is None or ident.status != "active" or ident.role != role:
        m = ctx.cvat.ensure_member(org.cvat_org_slug, username, role, email, uid)
        if ident is None:
            ident = CvatIdentityMapping(tenant_id=tenant_id, user_id=user_id, cvat_instance=org.cvat_instance, role=role)
            session.add(ident)
        ident.role, ident.cvat_user_id, ident.cvat_username, ident.status = role, m["user_id"], username, "active"
        session.flush()
    return ident


def ensure_member_identity(session: Session, ctx: Ctx, member: LabelingMember) -> CvatIdentityMapping:
    """CVAT user + org membership + IdP link for a portal member, created on first need (assignment or editor launch)."""
    org = session.get(TenantCvatOrg, (member.tenant_id, ctx.settings.cvat_instance))
    if org is None:
        raise NotFound("tenant has no CVAT organization yet")
    return _ensure_identity(session, ctx, org, member.tenant_id, member.user_id, cvat_username_for(member.user_id), member.email or "",
                            member.user_id, cvat_role_for(member.roles))


def assign_job(session: Session, ctx: Ctx, *, tenant_id: str, job_id: uuid.UUID, user_id: str, username: str, email: str = "",
               uid: str | None = None, role: str = "worker", expected_revision: int | None = None) -> LabelingJob:
    """Manual assignment: ensure CVAT membership (worker) for the user in the tenant's org, set CVAT assignee, record it."""
    job = session.scalar(select(LabelingJob).where(LabelingJob.tenant_id == tenant_id, LabelingJob.id == job_id))
    if job is None:
        raise NotFound("job not found for tenant")
    if expected_revision is not None and job.revision != expected_revision:
        raise Conflict(f"job revision is {job.revision}, expected {expected_revision}")
    org = session.get(TenantCvatOrg, (tenant_id, job.cvat_instance))
    ident = _ensure_identity(session, ctx, org, tenant_id, user_id, username, email, uid, role)
    ctx.cvat.set_job_assignee(org.cvat_org_slug, job.cvat_job_id, ident.cvat_user_id)
    job.assignee_user_id = user_id
    job.revision += 1
    session.flush()
    return job


def set_member(session: Session, ctx: Ctx, *, tenant_id: str, user_id: str, roles: list[str], email: str | None = None) -> LabelingMember:
    """Grant/modify access to labeling in a tenant. CVAT membership is provisioned lazily and re-synced here so a role
    change takes effect in CVAT immediately for users who already have an identity there."""
    bad = set(roles) - {"labeler", "reviewer", "creator", "admin"}
    if not roles or bad:
        raise Invalid(f"invalid roles {sorted(bad) or roles}")
    m = session.get(LabelingMember, (tenant_id, user_id))
    if m is None:
        m = LabelingMember(tenant_id=tenant_id, user_id=user_id, roles=sorted(set(roles)))
        session.add(m)
    m.roles, m.status, m.email, m.username = sorted(set(roles)), "active", email or m.email, cvat_username_for(user_id)
    session.flush()
    ident = session.scalar(select(CvatIdentityMapping).where(CvatIdentityMapping.tenant_id == tenant_id, CvatIdentityMapping.user_id == user_id,
                                                             CvatIdentityMapping.cvat_instance == ctx.settings.cvat_instance))
    if ident is not None and ident.status == "active":
        org = session.get(TenantCvatOrg, (tenant_id, ident.cvat_instance))
        ctx.cvat.ensure_member(org.cvat_org_slug, m.username, cvat_role_for(roles), m.email or "", user_id)
        ident.role = cvat_role_for(roles)
    return m


def remove_member(session: Session, ctx: Ctx, *, tenant_id: str, user_id: str) -> None:
    """Revoke access: portal membership, open job assignments, CVAT org membership (and account when it was the only one)."""
    m = session.get(LabelingMember, (tenant_id, user_id))
    if m is None:
        raise NotFound("member not found for tenant")
    m.status = "removed"
    idents = session.scalars(select(CvatIdentityMapping).where(CvatIdentityMapping.tenant_id == tenant_id, CvatIdentityMapping.user_id == user_id)).all()
    other_active = session.scalar(select(LabelingMember.tenant_id).where(LabelingMember.user_id == user_id, LabelingMember.status == "active").limit(1))
    for ident in idents:
        org = session.get(TenantCvatOrg, (tenant_id, ident.cvat_instance))
        for job in session.scalars(select(LabelingJob).where(LabelingJob.tenant_id == tenant_id, LabelingJob.assignee_user_id == user_id,
                                                              LabelingJob.portal_state.notin_(("approved", "published")))):
            ctx.cvat.set_job_assignee(org.cvat_org_slug, job.cvat_job_id, None)
            job.assignee_user_id = None
            job.revision += 1
        ctx.cvat.revoke_member(org.cvat_org_slug, ident.cvat_username, deactivate=other_active is None)
        ident.status = "revoked"
    session.flush()
