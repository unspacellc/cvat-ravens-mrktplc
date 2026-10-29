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

from . import operations, review, sendoff, service
from .adapters import IdentityContext, Principal
from .config import Settings
from .models import (
    CvatIdentityMapping,
    DatasetVersion,
    LabelingBatch,
    LabelingJob,
    LabelingMember,
    LabelingTask,
    Operation,
    ReviewEvent,
    ReviewIssue,
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


class IssueBody(BaseModel):
    frame: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000)
    position: list[float] | None = Field(default=None, min_length=4, max_length=4)
    blocking: bool = True


class TransitionBody(BaseModel):
    command: str
    expected_revision: int
    reason: str | None = Field(default=None, max_length=2000)
    reviewer_id: str | None = None
    issues: list[IssueBody] = []
    attestation: str | None = Field(default=None, max_length=2000)
    expected_snapshot_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    resolved_issue_ids: list[uuid.UUID] = []
    override: bool = False


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
        dv = s.scalar(select(DatasetVersion).where(DatasetVersion.tenant_id == p.tenant_id, DatasetVersion.batch_id == b.id))
        return {"id": str(b.id), "name": b.name, "lifecycle": b.lifecycle, "revision": b.revision, "job_counts": counts,
                "dataset_version": None if dv is None else {"id": str(dv.id), "status": dv.status},
                "predecessor_version_id": str(b.predecessor_version_id) if b.predecessor_version_id else None,
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

    @r.post("/jobs/{job_id}/transitions")
    def transition(job_id: uuid.UUID, body: TransitionBody, p: P, s: S):
        need(p, "labeler", "reviewer", "admin")
        scoped_job(s, p, job_id)  # tenant + visibility 404 first, so foreign jobs reveal nothing
        cmd = review.Command(name=body.command, expected_revision=body.expected_revision, reason=body.reason, reviewer_id=body.reviewer_id,
                             issues=[review.IssueInput(i.frame, i.text, i.position, i.blocking) for i in body.issues],
                             attestation=body.attestation, expected_snapshot_sha256=body.expected_snapshot_sha256,
                             resolved_issue_ids=body.resolved_issue_ids, override=body.override)
        try:
            job = review.transition(s, deps.ctx, actor=p, job_id=job_id, cmd=cmd)
            s.commit()
        except service.Forbidden as e:
            raise ApiError(403, "forbidden", str(e)) from None
        except service.Conflict as e:
            s.rollback()
            raise ApiError(409, "illegal_transition" if "cannot" in str(e) else "revision_conflict", str(e)) from None
        except service.Invalid as e:
            s.rollback()
            raise ApiError(422, "invalid_command", str(e)) from None
        except service.NotFound as e:
            raise ApiError(404, "not_found", str(e)) from None
        return {"id": str(job.id), "portal_state": job.portal_state, "assignee_user_id": job.assignee_user_id, "revision": job.revision}

    @r.get("/jobs/{job_id}/review")
    def job_review(job_id: uuid.UUID, p: P, s: S):
        """Evidence + open issues; for the assigned reviewer (or admin) also the hash of the content they would approve."""
        need(p, "labeler", "reviewer", "admin")
        job, _, _ = scoped_job(s, p, job_id)
        events = s.scalars(select(ReviewEvent).where(ReviewEvent.tenant_id == p.tenant_id, ReviewEvent.job_id == job.id).order_by(ReviewEvent.created_at))
        issues = s.scalars(select(ReviewIssue).where(ReviewIssue.tenant_id == p.tenant_id, ReviewIssue.job_id == job.id).order_by(ReviewIssue.created_at))
        out = {"portal_state": job.portal_state, "revision": job.revision, "live_snapshot_sha256": None,
               "events": [{"decision": e.decision, "actor_id": e.actor_id, "reason": e.reason, "annotation_revision": e.annotation_revision,
                           "created_at": e.created_at.isoformat()} for e in events],
               "issues": [{"id": str(i.id), "frame": i.frame, "position": i.position, "text": i.text, "blocking": i.blocking,
                           "status": i.status} for i in issues]}
        if job.portal_state == "in_review" and (job.assignee_user_id == p.user_id or "admin" in p.roles):
            out["live_snapshot_sha256"] = review.live_review_sha(deps.ctx, s, p.tenant_id, job.id)
        return out

    @r.post("/batches/{batch_id}/send-offs", status_code=202)
    def create_send_off(batch_id: uuid.UUID, p: P, s: S, idempotency_key: Annotated[str | None, Header()] = None):
        need(p, "admin")
        if not idempotency_key:
            raise ApiError(400, "idempotency_key_required", "Idempotency-Key header is required")
        try:
            dv, op = sendoff.request_send_off(s, deps.ctx, tenant_id=p.tenant_id, batch_id=batch_id, idempotency_key=idempotency_key)
            s.commit()
        except service.NotFound as e:
            raise ApiError(404, "not_found", str(e)) from None
        except sendoff.NotEligible as e:
            s.rollback()
            raise ApiError(409, "not_eligible", str(e), {"reasons": e.reasons}) from None
        except service.Conflict as e:
            s.rollback()
            raise ApiError(409, "conflict", str(e)) from None
        except operations.IdempotencyConflict as e:
            s.rollback()
            raise ApiError(409, "idempotency_conflict", str(e)) from None
        return {"dataset_version_id": str(dv.id), "operation_id": str(op.id)}

    @r.get("/dataset-versions/{version_id}")
    def dataset_version(version_id: uuid.UUID, p: P, s: S):
        need(p, "reviewer", "admin", "creator")
        dv = s.scalar(select(DatasetVersion).where(DatasetVersion.tenant_id == p.tenant_id, DatasetVersion.id == version_id))
        if dv is None:
            raise ApiError(404, "not_found", "dataset version not found")
        return {"id": str(dv.id), "batch_id": str(dv.batch_id), "status": dv.status, "predecessor_version_id": str(dv.predecessor_version_id) if dv.predecessor_version_id else None,
                "manifest_uri": dv.manifest_uri, "manifest_sha256": dv.manifest_sha256, "published_at": dv.published_at.isoformat() if dv.published_at else None}

    @r.post("/dataset-versions/{version_id}/revision-batches", status_code=202)
    def revision_batch(version_id: uuid.UUID, p: P, s: S, idempotency_key: Annotated[str | None, Header()] = None):
        need(p, "creator", "admin")
        if not idempotency_key:
            raise ApiError(400, "idempotency_key_required", "Idempotency-Key header is required")
        try:
            batch, op = service.create_revision_batch(s, deps.ctx, tenant_id=p.tenant_id, version_id=version_id, idempotency_key=idempotency_key)
            s.commit()
        except service.NotFound as e:
            raise ApiError(404, "not_found", str(e)) from None
        return {"batch_id": str(batch.id), "operation_id": str(op.id)}

    @r.get("/reviewers")
    def reviewers(p: P, s: S):
        """Who a submitter may pick as reviewer (no other member data is exposed to labelers)."""
        need(p, "labeler", "reviewer", "admin", "creator")
        rows = s.scalars(select(LabelingMember).where(LabelingMember.tenant_id == p.tenant_id, LabelingMember.status == "active",
                                                      LabelingMember.roles.any("reviewer")))
        return {"items": [{"user_id": m.user_id, "email": m.email} for m in rows if m.user_id != p.user_id]}

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
