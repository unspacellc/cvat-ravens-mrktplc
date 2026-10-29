"""Correction metrics for pre-labeled work. Versioned method, stated limitations, `None` instead of invented numbers.

correction_rate = (edited + deleted baseline predictions) / imported baseline predictions   (None when the denominator is 0)
Additions are reported separately. Greedy one-to-one IoU matching per frame; splits/merges are counted as ambiguous
rather than guessed. This measures change between the baseline and an approved snapshot; it is NOT proof that a reviewer
inspected every shape (the approval attestation covers a revision and frame range only).
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AnnotationSnapshot, LabelingJob, PrelabelImport, PrelabelRun, ReviewEvent
from .service import Ctx, NotFound

METHOD = {"name": "iou-greedy", "version": 1, "match_iou": 0.5, "unchanged_iou": 0.95}
LIMITATIONS = [
    "Baseline = normalized model output after confidence/label filtering, not the raw output.",
    "Per-frame greedy one-to-one IoU matching; a split or merge is flagged ambiguous, not resolved.",
    "Rectangles only; polygons, masks and tracks are not matched.",
    "Time-on-task is not instrumented and is not reported.",
    "Approval attests a job revision and frame range, not per-shape inspection.",
]


def iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def correction_metrics(baseline: list[dict], final: list[dict]) -> dict:
    """baseline: [{frame,label,box}], final: [{frame,label,box}] (labels by name)."""
    m = METHOD
    unchanged = edited = deleted = ambiguous = 0
    matched_final: set[int] = set()
    frames = {b["frame"] for b in baseline}
    for f in sorted(frames):
        bs = [(i, b) for i, b in enumerate(baseline) if b["frame"] == f]
        fs = [(j, s) for j, s in enumerate(final) if s["frame"] == f]
        pairs = sorted(((iou(b["box"], s["box"]), i, j) for i, b in bs for j, s in fs), reverse=True)
        used_b: set[int] = set()
        used_f: set[int] = set()
        for score, i, j in pairs:
            if score < m["match_iou"]:
                break
            if i in used_b or j in used_f:
                continue
            used_b.add(i), used_f.add(j)
            same = baseline[i]["label"] == final[j]["label"]
            if same and score >= m["unchanged_iou"]:
                unchanged += 1
            else:
                edited += 1
        for i, _ in bs:
            if i not in used_b:
                overlaps = [j for j, s in fs if iou(baseline[i]["box"], s["box"]) >= m["match_iou"]]
                if overlaps:
                    ambiguous += 1  # overlapped only by shapes already claimed by another baseline box
                else:
                    deleted += 1
        matched_final |= used_f
    added = sum(1 for j, s in enumerate(final) if j not in matched_final and not any(
        b["frame"] == s["frame"] and iou(b["box"], s["box"]) >= m["match_iou"] for b in baseline))
    n = len(baseline)
    return {"baseline": n, "unchanged": unchanged, "edited": edited, "deleted": deleted, "added": added, "ambiguous": ambiguous,
            "correction_rate": None if n == 0 else (edited + deleted) / n}


def run_metrics(session: Session, ctx: Ctx, *, tenant_id: str, run_id: uuid.UUID) -> dict:
    run = session.scalar(select(PrelabelRun).where(PrelabelRun.tenant_id == tenant_id, PrelabelRun.id == run_id))
    if run is None:
        raise NotFound("pre-label run not found for tenant")
    jobs_out, tot = [], {"baseline": 0, "unchanged": 0, "edited": 0, "deleted": 0, "added": 0, "ambiguous": 0}
    for led in session.scalars(select(PrelabelImport).where(PrelabelImport.tenant_id == tenant_id, PrelabelImport.run_id == run.id)):
        baseline = json.loads(ctx.store.get(led.baseline_uri)) if led.baseline_uri else []
        for job in session.scalars(select(LabelingJob).where(LabelingJob.tenant_id == tenant_id, LabelingJob.task_id == led.task_id).order_by(LabelingJob.start_frame)):
            jb = [b for b in baseline if job.start_frame <= b["frame"] <= job.stop_frame]
            entry = {"job_id": str(job.id), "portal_state": job.portal_state, "baseline": len(jb)}
            if job.approved_snapshot_id is None:
                entry["status"] = "awaiting_approval"
            else:
                snap = session.get(AnnotationSnapshot, job.approved_snapshot_id)
                doc = json.loads(ctx.store.get(snap.uri))
                final = [{"frame": s["frame"], "label": doc["labels"][str(s["label_id"])], "box": s["points"]} for s in doc["annotations"]["shapes"] if s["type"] == "rectangle"]
                res = correction_metrics(jb, final)
                ev = session.scalar(select(ReviewEvent).where(ReviewEvent.job_id == job.id, ReviewEvent.snapshot_id == snap.id, ReviewEvent.decision == "approve"))
                entry |= {"status": "measured", **res, "annotation_revision": snap.annotation_revision,
                          "attestation": ev.details.get("attestation") if ev else None, "reviewer_id": ev.actor_id if ev else None}
                for k in tot:
                    tot[k] += res[k]
            jobs_out.append(entry)
    measured = [j for j in jobs_out if j["status"] == "measured"]
    n = tot["baseline"]
    return {"run_id": str(run.id), "model": run.model_descriptor.get("artifact"), "method": METHOD, "limitations": LIMITATIONS,
            "jobs_total": len(jobs_out), "jobs_measured": len(measured), "totals": {**tot, "correction_rate": None if n == 0 else (tot["edited"] + tot["deleted"]) / n},
            "jobs": jobs_out}


__all__ = ["correction_metrics", "run_metrics", "METHOD", "LIMITATIONS", "iou"]
