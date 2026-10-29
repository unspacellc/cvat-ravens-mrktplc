"""Job projection: CVAT is authoritative for live stage/state; the portal records *observations* of it.

Observations never change `portal_state` (approvals are portal decisions, ADR/plan: out-of-band CVAT changes must not
fabricate an approval). `annotation_revision` bumps whenever CVAT reports a newer job `updated_date`, which is what
later invalidates stale approvals.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .models import LabelingJob


def _ts(v: str | None):
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


def apply_cvat_job(session: Session, job: LabelingJob, cvat_job: dict) -> bool:
    """Apply a freshly *fetched* CVAT job. Returns True if anything observable changed. Stale data never rolls back."""
    seen = _ts(cvat_job.get("updated_date"))
    changed = False
    if seen and job.cvat_updated_date and seen < job.cvat_updated_date:
        job.synced_at = datetime.now(UTC)  # confirmed in sync, but this snapshot is older than what we hold
        return False
    if seen and (job.cvat_updated_date is None or seen > job.cvat_updated_date):
        if job.cvat_updated_date is not None:
            job.annotation_revision += 1
        job.cvat_updated_date, changed = seen, True
    for attr, key in (("cvat_stage", "stage"), ("cvat_state", "state")):
        if getattr(job, attr) != cvat_job.get(key):
            setattr(job, attr, cvat_job.get(key))
            changed = True
    job.synced_at = datetime.now(UTC)
    session.flush()
    return changed


def apply_own_write(session: Session, job: LabelingJob, cvat_job: dict) -> None:
    """Record a CVAT job state that *the portal itself just wrote* (stage/state/assignee PATCH response). The new
    `updated_date` is adopted without bumping `annotation_revision`, otherwise every portal transition would look like
    an annotator edit. Content integrity is checked by snapshot hash, never by timestamp."""
    job.cvat_updated_date = _ts(cvat_job.get("updated_date")) or job.cvat_updated_date
    job.cvat_stage, job.cvat_state = cvat_job.get("stage"), cvat_job.get("state")
    job.synced_at = datetime.now(UTC)
    session.flush()
