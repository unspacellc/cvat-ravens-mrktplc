"""Review workflow against real CVAT: submit/reject/resubmit/approve/reopen with revision-bound evidence."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from api.labeling import review, service
from api.labeling.models import AnnotationSnapshot, ReviewEvent, ReviewIssue

from .review_harness import make_setup

pytestmark = pytest.mark.capability


@pytest.fixture
def rv(session_factory, cvat_token, clips):
    t = f"tenant-rv-{uuid.uuid4().hex[:8]}"
    st = make_setup(session_factory, cvat_token, clips, t)
    lab, rev = f"lab-{t}", f"rev-{t}"
    with session_factory() as s:
        job = st.job(s)
        service.assign_job(s, st.ctx, tenant_id=t, job_id=job.id, user_id=lab, username=service.cvat_username_for(lab), uid=lab)
        s.commit()
    st.lab, st.rev, st.adm, st.lab2 = lab, rev, f"adm-{t}", f"lab2-{t}"
    return st


def cmd(name, rev, **kw):
    return review.Command(name=name, expected_revision=rev, **kw)


def do(st, actor, name, **kw):
    """Run one transition in its own transaction; returns the refreshed job."""
    with st.sf() as s:
        job = st.job(s)
        out = review.transition(s, st.ctx, actor=actor, job_id=job.id, cmd=cmd(name, job.revision, **kw))
        s.commit()
        return out.portal_state


def test_full_cycle_reject_resubmit_approve_with_new_revision(rv):
    lab, rev = rv.actor(rv.lab, "labeler"), rv.actor(rv.rev, "reviewer")
    rv.draw(x=10)
    assert do(rv, lab, "submit", reviewer_id=rv.rev) == "in_review"
    with rv.sf() as s:
        job = rv.job(s)
        assert (job.assignee_user_id, job.cvat_stage, job.cvat_state) == (rv.rev, "validation", "new")
        first_snapshot = s.scalar(select(AnnotationSnapshot).where(AnnotationSnapshot.job_id == job.id, AnnotationSnapshot.reason == "submit"))
    # reject with a frame issue -> back to the labeler as rework
    assert do(rv, rev, "reject", reason="box too loose", issues=[review.IssueInput(frame=0, text="tighten", position=[10, 10, 50, 50])]) == "rework"
    with rv.sf() as s:
        job = rv.job(s)
        assert (job.assignee_user_id, job.cvat_stage, job.cvat_state) == (rv.lab, "annotation", "rejected")
        issue = s.scalar(select(ReviewIssue).where(ReviewIssue.job_id == job.id))
        assert issue.status == "open" and issue.cvat_issue_id  # mirrored into CVAT
        issue_id = issue.id
    # resubmitting without resolving the issue is refused
    with pytest.raises(service.Invalid):
        do(rv, lab, "submit", reviewer_id=rv.rev)
    rv.draw(x=20)  # the fix
    assert do(rv, lab, "submit", reviewer_id=rv.rev, resolved_issue_ids=[issue_id]) == "in_review"
    # approval must name the content that was reviewed; a stale hash is rejected
    with pytest.raises(service.Conflict, match="changed since you reviewed"):
        do(rv, rev, "approve", attestation="all frames reviewed", expected_snapshot_sha256="0" * 64)
    with rv.sf() as s:
        sha = review.live_review_sha(rv.ctx, s, rv.tenant, rv.job_ids[0])
    assert do(rv, rev, "approve", attestation="all frames reviewed", expected_snapshot_sha256=sha) == "approved"
    with rv.sf() as s:
        job = rv.job(s)
        approved = s.get(AnnotationSnapshot, job.approved_snapshot_id)
        assert approved.sha256 == sha and approved.sha256 != first_snapshot.sha256  # reapproval targets the NEW content
        assert (job.cvat_stage, job.cvat_state) == ("acceptance", "completed")
        decisions = [e.decision for e in s.scalars(select(ReviewEvent).where(ReviewEvent.job_id == job.id).order_by(ReviewEvent.created_at))]
    assert decisions == ["submit", "reject", "resubmit", "approve"]


def test_independence_roles_and_stale_revision(rv):
    lab, rev, adm = rv.actor(rv.lab, "labeler"), rv.actor(rv.rev, "reviewer"), rv.actor(rv.adm, "admin")
    rv.draw()
    with pytest.raises(service.Invalid, match="independent"):  # reviewer == annotator
        do(rv, lab, "submit", reviewer_id=rv.lab)
    with pytest.raises(service.Forbidden):  # someone who is not the assignee
        do(rv, rv.actor(rv.lab2, "labeler"), "submit", reviewer_id=rv.rev)
    with rv.sf() as s, pytest.raises(service.Conflict, match="revision"):
        review.transition(s, rv.ctx, actor=lab, job_id=rv.job_ids[0], cmd=cmd("submit", 999, reviewer_id=rv.rev))
    do(rv, lab, "submit", reviewer_id=rv.rev)
    with pytest.raises(service.Forbidden):  # annotator cannot approve their own work even with a reviewer role
        do(rv, rv.actor(rv.lab, "labeler", "reviewer"), "approve", attestation="x", expected_snapshot_sha256="0" * 64)
    with pytest.raises(service.Invalid, match="at least one"):
        do(rv, rev, "reject", issues=[])
    with pytest.raises(service.Forbidden):  # reopen is admin-only and only for approved work
        do(rv, rev, "reopen", reason="x")
    with pytest.raises(service.Invalid, match="override"):
        do(rv, adm, "reopen", override=True)  # override needs a reason


def test_editing_after_approval_invalidates_it_and_reopen_is_audited(rv):
    lab, rev, _adm = rv.actor(rv.lab, "labeler"), rv.actor(rv.rev, "reviewer"), rv.actor(rv.adm, "admin")
    rv.draw()
    do(rv, lab, "submit", reviewer_id=rv.rev)
    with rv.sf() as s:
        sha = review.live_review_sha(rv.ctx, s, rv.tenant, rv.job_ids[0])
    do(rv, rev, "approve", attestation="ok", expected_snapshot_sha256=sha)
    with rv.sf() as s:
        job = rv.job(s)
        from api.labeling.models import LabelingTask, TenantCvatOrg
        task, org = s.get(LabelingTask, job.task_id), s.get(TenantCvatOrg, (rv.tenant, "primary"))
        assert review.verify_approval(s, rv.ctx, job, task, org) is True  # portal's own writes do not look like edits
    rv.draw(x=30)  # out-of-band edit after approval
    with rv.sf() as s:
        job = rv.job(s)
        task, org = s.get(LabelingTask, job.task_id), s.get(TenantCvatOrg, (rv.tenant, "primary"))
        assert review.verify_approval(s, rv.ctx, job, task, org) is False
        s.commit()
        assert job.portal_state == "in_review" and job.approved_snapshot_id is None
    with rv.sf() as s:
        assert "approval_invalidated" in [e.decision for e in s.scalars(select(ReviewEvent).where(ReviewEvent.job_id == rv.job_ids[0]))]


def test_reopen_returns_to_labeler_and_evidence_is_append_only(rv):
    lab, rev, adm = rv.actor(rv.lab, "labeler"), rv.actor(rv.rev, "reviewer"), rv.actor(rv.adm, "admin")
    rv.draw()
    do(rv, lab, "submit", reviewer_id=rv.rev)
    with rv.sf() as s:
        sha = review.live_review_sha(rv.ctx, s, rv.tenant, rv.job_ids[0])
    do(rv, rev, "approve", attestation="ok", expected_snapshot_sha256=sha)
    assert do(rv, adm, "reopen", reason="customer found a missed car") == "rework"
    with rv.sf() as s:
        job = rv.job(s)
        assert job.assignee_user_id == rv.lab and job.approved_snapshot_id is None and (job.cvat_stage, job.cvat_state) == ("annotation", "rejected")
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(text("UPDATE annotation_snapshot SET sha256 = 'x' WHERE job_id = :j"), {"j": job.id})
        s.rollback()
    with rv.sf() as s, pytest.raises(DBAPIError, match="append-only"):
        s.execute(text("DELETE FROM review_event WHERE job_id = :j"), {"j": rv.job_ids[0]})


def test_labeler_loses_cvat_write_access_during_review(rv):
    """Server-enforced read/write restriction: after submit the annotator's own CVAT session cannot edit the job."""
    import httpx

    from .conftest import CVAT_URL, user_token
    rv.draw()
    do(rv, rv.actor(rv.lab, "labeler"), "submit", reviewer_id=rv.rev)
    tok = user_token(service.cvat_username_for(rv.lab))
    c = httpx.Client(base_url=CVAT_URL, headers={"Authorization": f"Token {tok}", "X-Organization": rv.slug}, timeout=60)
    with rv.sf() as s:
        cvat_job = rv.job(s).cvat_job_id
    assert c.patch(f"/api/jobs/{cvat_job}/annotations", params={"action": "create"}, json={"shapes": [], "tags": [], "tracks": [], "version": 0}).status_code in (403, 404)
    assert c.get(f"/api/jobs/{cvat_job}").status_code in (403, 404)
