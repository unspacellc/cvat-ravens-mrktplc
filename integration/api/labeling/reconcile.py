"""Periodic reconciliation: refetch CVAT state for active jobs. Repairs dropped/reordered/duplicated webhook deliveries."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from .cvat_client import CvatError
from .models import LabelingJob, TenantCvatOrg
from .operations import now
from .projection import apply_cvat_job
from .service import Ctx

ACTIVE_EXCLUDED = ("sent_off",)


def reconcile_tenant(session_factory, ctx: Ctx, tenant_id: str, *, stale_after_s: float = 0.0, limit: int = 500) -> dict:
    """Refetch jobs not synced within `stale_after_s`. Returns counters for the sweep."""
    stats = {"checked": 0, "changed": 0, "missing": 0, "errors": 0}
    cutoff = now() - timedelta(seconds=stale_after_s)
    with session_factory() as s:
        org = s.get(TenantCvatOrg, (tenant_id, ctx.settings.cvat_instance))
        if org is None:
            return stats
        jobs = s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == tenant_id, LabelingJob.portal_state.notin_(ACTIVE_EXCLUDED))
                         .where((LabelingJob.synced_at.is_(None)) | (LabelingJob.synced_at <= cutoff))
                         .order_by(LabelingJob.synced_at.nulls_first()).limit(limit).with_for_update(skip_locked=True)).all()
        for job in jobs:
            stats["checked"] += 1
            try:
                remote = ctx.cvat.get_job(org.cvat_org_slug, job.cvat_job_id)
            except CvatError as e:
                if e.status == 404:
                    job.cvat_state, job.synced_at = "missing", now()
                    stats["missing"] += 1
                else:
                    stats["errors"] += 1
                continue
            stats["changed"] += int(apply_cvat_job(s, job, remote))
        s.commit()
    return stats


def reconcile_all(session_factory, ctx: Ctx, **kw) -> dict:
    with session_factory() as s:
        tenants = [t for (t,) in s.execute(select(TenantCvatOrg.tenant_id).where(TenantCvatOrg.cvat_instance == ctx.settings.cvat_instance))]
    total = {"checked": 0, "changed": 0, "missing": 0, "errors": 0}
    for t in tenants:
        for k, v in reconcile_tenant(session_factory, ctx, t, **kw).items():
            total[k] += v
    return total
