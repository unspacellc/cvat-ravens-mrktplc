"""Review workflow: explicit submit / reject / approve / reopen commands with revision-bound, immutable evidence.

Authority: the portal decides, CVAT enforces. While a job is in review the *reviewer* is the CVAT assignee (the
labeler loses write access); on rejection or reopen the labeler gets it back; approved work moves to acceptance/completed.
Approval is bound to a content hash of the job's annotations (not a timestamp), so any later edit is detectable even if
the portal's own CVAT writes touched `updated_date`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters import Principal
from .cvat_client import CvatError
from .models import (
    AnnotationSnapshot,
    LabelingBatch,
    LabelingJob,
    LabelingMember,
    LabelingTask,
    ReviewEvent,
    ReviewIssue,
    TenantCvatOrg,
)
from .projection import apply_own_write
from .service import Conflict, Ctx, Forbidden, Invalid, NotFound, ensure_member_identity

log = logging.getLogger(__name__)
SNAPSHOT_FORMAT = "labeling.job_annotations.v1"
FROZEN_LIFECYCLES = ("sending_off", "publishing", "sent_off")
COMMANDS = ("submit", "reject", "approve", "reopen")


@dataclass
class IssueInput:
    frame: int
    text: str
    position: list[float] | None = None
    blocking: bool = True


@dataclass
class Command:
    name: str
    expected_revision: int
    reason: str | None = None
    reviewer_id: str | None = None  # submit
    issues: list[IssueInput] = field(default_factory=list)  # reject
    attestation: str | None = None  # approve
    expected_snapshot_sha256: str | None = None  # approve: the content the reviewer actually looked at
    resolved_issue_ids: list[uuid.UUID] = field(default_factory=list)  # submit from rework
    override: bool = False  # admin only: waive reviewer independence (audited, reason required)


def canonical_snapshot(ctx: Ctx, slug: str, job: LabelingJob, task: LabelingTask) -> tuple[bytes, str]:
    """Deterministic bytes + sha256 of the job's current annotations (CVAT's per-save `version` counter excluded)."""
    ann = ctx.cvat.get_job_annotations(slug, job.cvat_job_id)
    labels = ctx.cvat.task_labels(slug, task.cvat_task_id)
    body = {k: sorted(ann.get(k) or [], key=lambda x: (x.get("frame", 0), x.get("id") or 0)) for k in ("tags", "shapes", "tracks")}
    doc = {"format": SNAPSHOT_FORMAT, "job": {"cvat_job_id": job.cvat_job_id, "start_frame": job.start_frame, "stop_frame": job.stop_frame},
           "labels": {str(k): v for k, v in sorted(labels.items())}, "annotations": body}
    data = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    return data, hashlib.sha256(data).hexdigest()


def take_snapshot(session: Session, ctx: Ctx, job: LabelingJob, task: LabelingTask, slug: str, reason: str) -> AnnotationSnapshot:
    data, sha = canonical_snapshot(ctx, slug, job, task)
    uri, _ = ctx.store.put_if_absent(f"snapshots/{job.tenant_id}/{job.id}/{sha}.json", data)
    snap = AnnotationSnapshot(tenant_id=job.tenant_id, job_id=job.id, annotation_revision=job.annotation_revision, reason=reason,
                              format=SNAPSHOT_FORMAT, uri=uri, sha256=sha, bytes=len(data))
    session.add(snap)
    session.flush()
    return snap


def _load(session: Session, tenant_id: str, job_id: uuid.UUID):
    row = session.execute(
        select(LabelingJob, LabelingTask, LabelingBatch)
        .join(LabelingTask, (LabelingTask.tenant_id == LabelingJob.tenant_id) & (LabelingTask.id == LabelingJob.task_id))
        .join(LabelingBatch, (LabelingBatch.tenant_id == LabelingTask.tenant_id) & (LabelingBatch.id == LabelingTask.batch_id))
        .where(LabelingJob.tenant_id == tenant_id, LabelingJob.id == job_id).with_for_update(of=LabelingJob)).first()
    if row is None:
        raise NotFound("job not found for tenant")
    return row


def _cvat_user(session: Session, ctx: Ctx, tenant_id: str, user_id: str | None) -> int | None:
    if user_id is None:
        return None
    member = session.get(LabelingMember, (tenant_id, user_id))
    if member is None or member.status != "active":
        return None
    return ensure_member_identity(session, ctx, member).cvat_user_id


def _event(session: Session, job: LabelingJob, actor: str, decision: str, reason: str | None, snap: AnnotationSnapshot | None, **details) -> ReviewEvent:
    ev = ReviewEvent(tenant_id=job.tenant_id, job_id=job.id, actor_id=actor, decision=decision, reason=reason,
                     annotation_revision=job.annotation_revision, snapshot_id=snap.id if snap else None, details=details)
    session.add(ev)
    session.flush()
    return ev


def open_blocking_issues(session: Session, job: LabelingJob) -> list[ReviewIssue]:
    return list(session.scalars(select(ReviewIssue).where(ReviewIssue.tenant_id == job.tenant_id, ReviewIssue.job_id == job.id,
                                                          ReviewIssue.status == "open", ReviewIssue.blocking.is_(True))))


def _mirror_issue(ctx: Ctx, slug: str, job: LabelingJob, issue: ReviewIssue) -> None:
    """Best-effort: show the issue inside CVAT too. The portal row is authoritative for gating."""
    try:
        issue.cvat_issue_id = ctx.cvat.create_issue(slug, job.cvat_job_id, issue.frame, issue.position or [0, 0, 1, 1], issue.text)["id"]
    except CvatError as e:
        log.warning("could not mirror review issue to CVAT: %s", e)


def resolve_issues(session: Session, tenant_id: str, job: LabelingJob, issue_ids: list[uuid.UUID], user_id: str) -> None:
    for iid in issue_ids:
        issue = session.get(ReviewIssue, iid)
        if issue is None or issue.tenant_id != tenant_id or issue.job_id != job.id:
            raise NotFound(f"issue {iid} not found for job")
        issue.status, issue.resolved_by = "resolved", user_id
    session.flush()


def transition(session: Session, ctx: Ctx, *, actor: Principal, job_id: uuid.UUID, cmd: Command) -> LabelingJob:
    if ctx.store is None:
        raise RuntimeError("snapshot store is not configured")
    if cmd.name not in COMMANDS:
        raise Invalid(f"unknown command {cmd.name!r}")
    job, task, batch = _load(session, actor.tenant_id, job_id)
    if job.revision != cmd.expected_revision:
        raise Conflict(f"job revision is {job.revision}, expected {cmd.expected_revision}")
    if batch.lifecycle in FROZEN_LIFECYCLES:
        raise Conflict("batch is frozen for send-off; start a revision batch to change published work")
    org = session.get(TenantCvatOrg, (job.tenant_id, job.cvat_instance))
    is_admin = "admin" in actor.roles
    if cmd.override and not (is_admin and cmd.reason):
        raise Invalid("override requires an admin and a reason")
    is_assignee = job.assignee_user_id == actor.user_id
    handler = {"submit": _submit, "reject": _reject, "approve": _approve, "reopen": _reopen}[cmd.name]
    handler(session, ctx, actor, job, task, org, cmd, is_admin, is_assignee)
    job.revision += 1
    session.flush()
    return job


def _cvat_patch(ctx: Ctx, session: Session, org: TenantCvatOrg, job: LabelingJob, **fields) -> None:
    apply_own_write(session, job, ctx.cvat.update_job(org.cvat_org_slug, job.cvat_job_id, **fields))


def _submit(session, ctx, actor, job, task, org, cmd, is_admin, is_assignee):
    if job.portal_state not in ("created", "annotating", "rework"):
        raise Conflict(f"cannot submit a job in state {job.portal_state}")
    if not (is_assignee or is_admin):
        raise Forbidden("only the assignee (or an admin) can submit")
    if not cmd.reviewer_id:
        raise Invalid("reviewer_id is required")
    labeler = job.labeler_user_id if job.portal_state == "rework" else job.assignee_user_id
    if labeler is None:
        raise Invalid("job has no annotator of record; assign it before submitting")
    if cmd.reviewer_id == labeler and not cmd.override:
        raise Invalid("reviewer must be independent of the annotator (admin override requires a reason)")
    reviewer = session.get(LabelingMember, (job.tenant_id, cmd.reviewer_id))
    if reviewer is None or reviewer.status != "active" or not ({"reviewer", "admin"} & set(reviewer.roles)):
        raise Invalid("reviewer must be an active reviewer in this tenant")
    if job.portal_state == "rework":
        resolve_issues(session, job.tenant_id, job, cmd.resolved_issue_ids, actor.user_id)
        if (left := open_blocking_issues(session, job)):
            raise Invalid(f"{len(left)} blocking issue(s) are still open", ) 
    snap = take_snapshot(session, ctx, job, task, org.cvat_org_slug, "submit")
    decision = "resubmit" if job.portal_state == "rework" else "submit"
    reviewer_cvat = _cvat_user(session, ctx, job.tenant_id, cmd.reviewer_id)
    _cvat_patch(ctx, session, org, job, stage="validation", state="new", assignee=reviewer_cvat)
    job.labeler_user_id, job.reviewer_user_id, job.assignee_user_id, job.portal_state = labeler, cmd.reviewer_id, cmd.reviewer_id, "in_review"
    _event(session, job, actor.user_id, decision, cmd.reason, snap, reviewer_id=cmd.reviewer_id, override=cmd.override,
           resolved_issues=[str(i) for i in cmd.resolved_issue_ids])


def _review_guard(job, actor, is_admin, is_assignee, cmd):
    if job.portal_state != "in_review":
        raise Conflict(f"cannot {cmd.name} a job in state {job.portal_state}")
    if not (is_assignee or is_admin):
        raise Forbidden("only the assigned reviewer (or an admin) can decide")
    if actor.user_id == job.labeler_user_id and not cmd.override:
        raise Forbidden("the annotator cannot review their own work (admin override requires a reason)")


def _reject(session, ctx, actor, job, task, org, cmd, is_admin, is_assignee):
    _review_guard(job, actor, is_admin, is_assignee, cmd)
    if not cmd.issues:
        raise Invalid("a rejection must include at least one frame issue")
    snap = take_snapshot(session, ctx, job, task, org.cvat_org_slug, "reject")  # includes any reviewer edits
    created = []
    for i in cmd.issues:
        row = ReviewIssue(tenant_id=job.tenant_id, job_id=job.id, frame=i.frame, position=i.position, text=i.text, blocking=i.blocking,
                          created_by=actor.user_id)
        session.add(row)
        session.flush()
        _mirror_issue(ctx, org.cvat_org_slug, job, row)
        created.append(str(row.id))
    labeler_cvat = _cvat_user(session, ctx, job.tenant_id, job.labeler_user_id)
    _cvat_patch(ctx, session, org, job, stage="annotation", state="rejected", assignee=labeler_cvat)
    job.assignee_user_id, job.portal_state = (job.labeler_user_id if labeler_cvat else None), "rework"
    _event(session, job, actor.user_id, "reject", cmd.reason, snap, issue_ids=created)


def _approve(session, ctx, actor, job, task, org, cmd, is_admin, is_assignee):
    _review_guard(job, actor, is_admin, is_assignee, cmd)
    if not (cmd.attestation or "").strip():
        raise Invalid("approval requires a review attestation")
    if (left := open_blocking_issues(session, job)):
        raise Conflict(f"{len(left)} blocking issue(s) are unresolved")
    data, live_sha = canonical_snapshot(ctx, org.cvat_org_slug, job, task)
    if cmd.expected_snapshot_sha256 != live_sha:
        raise Conflict("annotations changed since you reviewed them; re-open the job and approve the current revision")
    snap = take_snapshot(session, ctx, job, task, org.cvat_org_slug, "approve")
    _cvat_patch(ctx, session, org, job, stage="acceptance", state="completed")
    _, after = canonical_snapshot(ctx, org.cvat_org_slug, job, task)
    if after != snap.sha256:  # content moved between snapshot and completion: do not leave a false approval
        _cvat_patch(ctx, session, org, job, stage="validation", state="in progress")
        raise Conflict("annotations changed during approval; review the current revision")
    job.approved_snapshot_id, job.portal_state = snap.id, "approved"
    _event(session, job, actor.user_id, "approve", cmd.reason, snap, attestation=cmd.attestation, override=cmd.override)


def _reopen(session, ctx, actor, job, task, org, cmd, is_admin, is_assignee):
    if not is_admin:
        raise Forbidden("only an admin can reopen approved work")
    if job.portal_state != "approved":
        raise Conflict(f"cannot reopen a job in state {job.portal_state}")
    if not (cmd.reason or "").strip():
        raise Invalid("reopen requires a reason")
    snap = take_snapshot(session, ctx, job, task, org.cvat_org_slug, "reopen")
    labeler_cvat = _cvat_user(session, ctx, job.tenant_id, job.labeler_user_id)
    _cvat_patch(ctx, session, org, job, stage="annotation", state="rejected", assignee=labeler_cvat)
    job.assignee_user_id, job.portal_state, job.approved_snapshot_id = (job.labeler_user_id if labeler_cvat else None), "rework", None
    _event(session, job, actor.user_id, "reopen", cmd.reason, snap)


def live_review_sha(ctx: Ctx, session: Session, tenant_id: str, job_id: uuid.UUID) -> str:
    """What a reviewer is about to approve (computed, not stored). Passed back as expected_snapshot_sha256."""
    job, task, _ = _load(session, tenant_id, job_id)
    org = session.get(TenantCvatOrg, (tenant_id, job.cvat_instance))
    return canonical_snapshot(ctx, org.cvat_org_slug, job, task)[1]


def verify_approval(session: Session, ctx: Ctx, job: LabelingJob, task: LabelingTask, org: TenantCvatOrg) -> bool:
    """True if the job's live annotations still hash to its approved snapshot. If not, the approval is invalidated and
    the job returns to review (never silently published)."""
    snap = session.get(AnnotationSnapshot, job.approved_snapshot_id) if job.approved_snapshot_id else None
    if snap is None:
        return False
    _, live = canonical_snapshot(ctx, org.cvat_org_slug, job, task)
    if live == snap.sha256:
        return True
    job.portal_state, job.approved_snapshot_id = "in_review", None
    job.annotation_revision += 1
    job.revision += 1
    _event(session, job, "system", "approval_invalidated", "annotations changed after approval", None, previous_snapshot=snap.sha256, live_sha256=live)
    session.flush()
    return False
