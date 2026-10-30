"""Operational metrics for the board: throughput, rejection, queue age, pre-label duration. Everything here is computed from
portal records. Time-on-task is NOT instrumented, so it is reported as unavailable rather than estimated."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import LabelingJob, LabelingTask, PrelabelRun, ReviewEvent

OPEN_STATES = ("created", "prelabeling", "annotating", "in_review", "rework", "approved")


def overview(session: Session, *, tenant_id: str, days: int = 14, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    by_state = dict(session.execute(select(LabelingJob.portal_state, func.count()).where(LabelingJob.tenant_id == tenant_id)
                                    .group_by(LabelingJob.portal_state)).all())

    events = session.execute(select(ReviewEvent.decision, func.date(ReviewEvent.created_at), func.count()).where(
        ReviewEvent.tenant_id == tenant_id, ReviewEvent.created_at >= since, ReviewEvent.decision.in_(("approve", "reject", "submit", "resubmit")))
        .group_by(ReviewEvent.decision, func.date(ReviewEvent.created_at))).all()
    approved_by_day: dict[str, int] = {}
    totals = {"approve": 0, "reject": 0}
    for decision, day, n in events:
        if decision == "approve":
            approved_by_day[str(day)] = approved_by_day.get(str(day), 0) + n
        if decision in totals:
            totals[decision] += n
    decided = totals["approve"] + totals["reject"]

    queue = {}
    for state, oldest, n in session.execute(select(LabelingJob.portal_state, func.min(LabelingJob.updated_at), func.count()).where(
            LabelingJob.tenant_id == tenant_id, LabelingJob.portal_state.in_(OPEN_STATES)).group_by(LabelingJob.portal_state)).all():
        queue[state] = {"jobs": n, "oldest_age_s": max(0, int((now - oldest).total_seconds()))}

    runs = session.scalars(select(PrelabelRun).where(PrelabelRun.tenant_id == tenant_id, PrelabelRun.created_at >= since)).all()
    done = [r for r in runs if r.status == "succeeded"]
    durations = [(r.updated_at - r.created_at).total_seconds() for r in done]

    return {
        "window_days": days,
        "jobs_by_state": by_state,
        "throughput": {"approved_per_day": dict(sorted(approved_by_day.items())), "approved_total": totals["approve"]},
        "rejection": {"rejected": totals["reject"], "approved": totals["approve"], "rate": round(totals["reject"] / decided, 3) if decided else None},
        "queue_age": queue,
        "prelabel": {"runs": len(runs), "by_status": {st: sum(1 for r in runs if r.status == st) for st in {r.status for r in runs}},
                     "inference_duration_s": {"avg": round(sum(durations) / len(durations), 1), "max": round(max(durations), 1)} if durations else None,
                     "correction_metrics": "per run: GET /prelabel-runs/{id}/metrics (measured against the approved snapshot)"},
        "time_on_task": {"available": False, "reason": "not instrumented; no estimate is shown"},
        "tasks_by_status": dict(session.execute(select(LabelingTask.status, func.count()).where(LabelingTask.tenant_id == tenant_id)
                                                 .group_by(LabelingTask.status)).all()),
    }
