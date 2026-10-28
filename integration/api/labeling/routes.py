"""Portal routes under /api/v1/labeling. Identity comes from a verified bearer token; tenant and roles from server-side
membership. Every query is filtered by the resolved tenant, and labelers can only ever see jobs assigned to them."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from . import service
from .adapters import IdentityContext, Principal
from .config import Settings
from .models import (
    CvatIdentityMapping,
    LabelingBatch,
    LabelingJob,
    LabelingMember,
    LabelingTask,
    Operation,
    TenantCvatOrg,
)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def error_response(request: Request, exc: ApiError) -> JSONResponse:
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    return JSONResponse(status_code=exc.status, headers={"x-request-id": rid},
                        content={"error": exc.message, "statusCode": exc.status, "code": exc.code, "details": exc.details, "requestId": rid})


@dataclass
class Deps:
    settings: Settings
    session_factory: sessionmaker[Session]
    identity: IdentityContext
    ctx: service.Ctx


class AssignBody(BaseModel):
    user_id: str
    expected_revision: int


class BatchBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    asset_ids: list[str] = Field(min_length=1)
    schema_version_id: uuid.UUID


class MemberBody(BaseModel):
    roles: list[str]
    email: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def job_view(job: LabelingJob, task: LabelingTask, batch: LabelingBatch) -> dict:
    age = None if job.synced_at is None else max(0, int((_now() - job.synced_at).total_seconds()))
    return {
        "id": str(job.id), "batch_id": str(batch.id), "batch_name": batch.name, "task_id": str(task.id),
        "source_asset_id": task.source_asset_id, "start_frame": job.start_frame, "stop_frame": job.stop_frame,
        "assignee_user_id": job.assignee_user_id, "portal_state": job.portal_state, "cvat_stage": job.cvat_stage,
        "cvat_state": job.cvat_state, "revision": job.revision, "sync_age_s": age,
        "due_date": batch.due_date.isoformat() if batch.due_date else None, "priority": batch.priority,
    }


def build_router(deps: Deps) -> APIRouter:
    r = APIRouter(prefix="/api/v1/labeling")

    def principal(authorization: Annotated[str | None, Header()] = None,
                  x_labeling_tenant: Annotated[str | None, Header()] = None) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise ApiError(401, "unauthenticated", "missing bearer token")
        try:
            return deps.identity.resolve(authorization[7:].strip(), x_labeling_tenant)
        except PermissionError as e:
            msg = str(e)
            if "tenant selection" in msg:
                raise ApiError(409, "tenant_selection_required", "send X-Labeling-Tenant") from None
            if "membership" in msg:
                raise ApiError(403, "not_a_member", "no active labeling membership") from None
            raise ApiError(401, "invalid_token", "invalid or expired token") from None

    P = Annotated[Principal, Depends(principal)]

    def need(p: Principal, *roles: str) -> None:
        if not (p.roles & set(roles)):
            raise ApiError(403, "forbidden", "insufficient role")

    def db():
        with deps.session_factory() as s:
            yield s

    S = Annotated[Session, Depends(db)]

    def scoped_job(s: Session, p: Principal, job_id: uuid.UUID) -> tuple[LabelingJob, LabelingTask, LabelingBatch]:
        """Tenant filter first; labelers additionally need to be the assignee. Misses are 404 (no existence oracle)."""
        q = (select(LabelingJob, LabelingTask, LabelingBatch)
             .join(LabelingTask, (LabelingTask.tenant_id == LabelingJob.tenant_id) & (LabelingTask.id == LabelingJob.task_id))
             .join(LabelingBatch, (LabelingBatch.tenant_id == LabelingTask.tenant_id) & (LabelingBatch.id == LabelingTask.batch_id))
             .where(LabelingJob.tenant_id == p.tenant_id, LabelingJob.id == job_id))
        if not (p.roles & {"reviewer", "admin", "creator"}):
            q = q.where(LabelingJob.assignee_user_id == p.user_id)
        row = s.execute(q).first()
        if row is None:
            raise ApiError(404, "not_found", "job not found")
        return row

    def bearer(authorization: str | None) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise ApiError(401, "unauthenticated", "missing bearer token")
        return authorization[7:].strip()

    @r.get("/me/tenants")
    def my_tenants(authorization: Annotated[str | None, Header()] = None):
        """Token-only: lets a multi-tenant user choose a tenant before any tenant-scoped call."""
        try:
            members = deps.identity.memberships(bearer(authorization))
        except PermissionError:
            raise ApiError(401, "invalid_token", "invalid or expired token") from None
        return {"items": [{"tenant_id": m.tenant_id, "roles": sorted(m.roles)} for m in members]}

    @r.get("/me")
    def me(p: P):
        return {"user_id": p.user_id, "tenant_id": p.tenant_id, "roles": sorted(p.roles), "email": p.email}

    @r.get("/jobs")
    def list_jobs(p: P, s: S, state: str | None = None, assignee: str | None = None, batch_id: uuid.UUID | None = None,
                  limit: Annotated[int, Query(ge=1, le=200)] = 50, cursor: Annotated[int, Query(ge=0)] = 0):
        need(p, "labeler", "reviewer", "admin", "creator")
        q = (select(LabelingJob, LabelingTask, LabelingBatch)
             .join(LabelingTask, (LabelingTask.tenant_id == LabelingJob.tenant_id) & (LabelingTask.id == LabelingJob.task_id))
             .join(LabelingBatch, (LabelingBatch.tenant_id == LabelingTask.tenant_id) & (LabelingBatch.id == LabelingTask.batch_id))
             .where(LabelingJob.tenant_id == p.tenant_id))
        if not (p.roles & {"reviewer", "admin", "creator"}):
            q = q.where(LabelingJob.assignee_user_id == p.user_id)  # labelers: own work only, whatever the filters say
        elif assignee:
            q = q.where(LabelingJob.assignee_user_id == (p.user_id if assignee == "me" else assignee))
        if state:
            q = q.where(LabelingJob.portal_state == state)
        if batch_id:
            q = q.where(LabelingBatch.id == batch_id)
        rows = s.execute(q.order_by(LabelingBatch.priority.desc(), LabelingBatch.created_at, LabelingJob.start_frame, LabelingJob.id)
                         .offset(cursor).limit(limit + 1)).all()
        items = [job_view(*row) for row in rows[:limit]]
        return {"items": items, "next_cursor": cursor + limit if len(rows) > limit else None}

    @r.get("/batches")
    def list_batches(p: P, s: S):
        need(p, "reviewer", "admin", "creator")
        rows = s.scalars(select(LabelingBatch).where(LabelingBatch.tenant_id == p.tenant_id).order_by(LabelingBatch.created_at.desc()).limit(200))
        return {"items": [{"id": str(b.id), "name": b.name, "lifecycle": b.lifecycle, "priority": b.priority,
                           "created_at": b.created_at.isoformat()} for b in rows]}

    @r.get("/batches/{batch_id}")
    def batch_detail(batch_id: uuid.UUID, p: P, s: S):
        need(p, "reviewer", "admin", "creator")
        b = s.scalar(select(LabelingBatch).where(LabelingBatch.tenant_id == p.tenant_id, LabelingBatch.id == batch_id))
        if b is None:
            raise ApiError(404, "not_found", "batch not found")
        tasks = s.scalars(select(LabelingTask).where(LabelingTask.tenant_id == p.tenant_id, LabelingTask.batch_id == b.id)).all()
        counts = dict(s.execute(select(LabelingJob.portal_state, func.count()).join(
            LabelingTask, (LabelingTask.tenant_id == LabelingJob.tenant_id) & (LabelingTask.id == LabelingJob.task_id))
            .where(LabelingJob.tenant_id == p.tenant_id, LabelingTask.batch_id == b.id).group_by(LabelingJob.portal_state)).all())
        op = s.scalar(select(Operation).where(Operation.tenant_id == p.tenant_id, Operation.subject_id == b.id).order_by(Operation.created_at.desc()))
        return {"id": str(b.id), "name": b.name, "lifecycle": b.lifecycle, "revision": b.revision, "job_counts": counts,
                "tasks": [{"id": str(t.id), "source_asset_id": t.source_asset_id, "status": t.status} for t in tasks],
                "operation": None if op is None else {"id": str(op.id), "state": op.state, "error": op.error}}

    @r.post("/batches", status_code=202)
    def create_batch(body: BatchBody, p: P, s: S, idempotency_key: Annotated[str | None, Header()] = None):
        need(p, "creator", "admin")
        if not idempotency_key:
            raise ApiError(400, "idempotency_key_required", "Idempotency-Key header is required")
        try:
            batch, op = service.create_batch(s, deps.ctx, tenant_id=p.tenant_id, name=body.name, asset_ids=body.asset_ids,
                                             schema_version_id=body.schema_version_id, idempotency_key=idempotency_key)
        except service.NotFound as e:
            raise ApiError(404, "not_found", str(e)) from None
        s.commit()
        return {"batch_id": str(batch.id), "operation_id": str(op.id)}

    @r.get("/operations/{op_id}")
    def get_operation(op_id: uuid.UUID, p: P, s: S):
        need(p, "reviewer", "admin", "creator")
        op = s.scalar(select(Operation).where(Operation.tenant_id == p.tenant_id, Operation.id == op_id))
        if op is None:
            raise ApiError(404, "not_found", "operation not found")
        return {"id": str(op.id), "kind": op.kind, "state": op.state, "attempts": op.attempts, "error": op.error,
                "progress": {k: v for k, v in (op.progress or {}).items() if k != "payload"}}

    @r.post("/jobs/{job_id}/assignment")
    def assign(job_id: uuid.UUID, body: AssignBody, p: P, s: S):
        need(p, "creator", "admin")
        member = s.get(LabelingMember, (p.tenant_id, body.user_id))  # tenant-scoped lookup: other tenants' users do not exist here
        if member is None or member.status != "active" or not ({"labeler", "reviewer"} & set(member.roles)):
            raise ApiError(422, "invalid_assignee", "assignee must be an active labeler or reviewer in this tenant")
        scoped_job(s, p, job_id)  # tenant check + 404
        try:
            job = service.assign_job(s, deps.ctx, tenant_id=p.tenant_id, job_id=job_id, user_id=member.user_id,
                                     username=service.cvat_username_for(member.user_id), email=member.email or "", uid=member.user_id,
                                     role=service.cvat_role_for(member.roles), expected_revision=body.expected_revision)
        except service.Conflict as e:
            raise ApiError(409, "revision_conflict", str(e)) from None
        s.commit()
        return {"id": str(job.id), "assignee_user_id": job.assignee_user_id, "revision": job.revision}

    @r.post("/jobs/{job_id}/editor-launch")
    def editor_launch(job_id: uuid.UUID, p: P, s: S):
        need(p, "labeler", "reviewer", "admin")
        job, task, _ = scoped_job(s, p, job_id)
        member = s.get(LabelingMember, (p.tenant_id, p.user_id))
        try:
            ident = service.ensure_member_identity(s, deps.ctx, member)
        except service.NotFound as e:
            raise ApiError(409, "not_provisioned", str(e)) from None
        org = s.get(TenantCvatOrg, (p.tenant_id, job.cvat_instance))
        live = deps.ctx.cvat.get_job(org.cvat_org_slug, job.cvat_job_id)  # authoritative, not the projection
        if "admin" not in p.roles:  # everyone else opens only what is assigned to them, in the portal and in CVAT
            if job.assignee_user_id != p.user_id:
                raise ApiError(403, "not_assigned", "job is not assigned to you")
            if (live.get("assignee") or {}).get("id") != ident.cvat_user_id:
                raise ApiError(409, "assignment_stale", "assignment is out of sync; try again shortly")
        s.commit()
        nxt = f"/tasks/{task.cvat_task_id}/jobs/{job.cvat_job_id}?org={org.cvat_org_slug}"  # server-built; never client supplied
        url = f"{deps.settings.cvat_public_url.rstrip('/')}/api/auth/social/oidc/{deps.settings.oidc_provider_id}/login/?" + urlencode(
            {"process": "login", "next": nxt})
        return {"url": url, "job_id": str(job.id)}

    @r.get("/members")
    def list_members(p: P, s: S):
        need(p, "admin", "creator")
        rows = s.scalars(select(LabelingMember).where(LabelingMember.tenant_id == p.tenant_id, LabelingMember.status == "active"))
        return {"items": [{"user_id": m.user_id, "roles": m.roles, "email": m.email} for m in rows]}

    @r.put("/members/{user_id}")
    def put_member(user_id: str, body: MemberBody, p: P, s: S):
        need(p, "admin")
        try:
            m = service.set_member(s, deps.ctx, tenant_id=p.tenant_id, user_id=user_id, roles=body.roles, email=body.email)
        except service.Invalid as e:
            raise ApiError(422, "invalid_roles", str(e)) from None
        s.commit()
        return {"user_id": m.user_id, "roles": m.roles}

    @r.delete("/members/{user_id}", status_code=204)
    def delete_member(user_id: str, p: P, s: S):
        need(p, "admin")
        if user_id == p.user_id:
            raise ApiError(422, "cannot_remove_self", "an admin cannot remove themself")
        try:
            service.remove_member(s, deps.ctx, tenant_id=p.tenant_id, user_id=user_id)
        except service.NotFound:
            raise ApiError(404, "not_found", "member not found") from None
        s.commit()

    return r


__all__ = ["ApiError", "Deps", "build_router", "error_response", "CvatIdentityMapping"]
