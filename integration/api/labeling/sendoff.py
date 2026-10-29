"""Guarded send-off: eligibility -> write barrier -> content re-verification -> export -> immutable manifest ->
idempotent catalog registration -> Sent off.

The `send_off` operation records each completed step in `Operation.progress`, and every external side effect is
find-before-create (objects are adopted by key, the dataset version id is allocated up front, registration is idempotent
on that id), so any crash or retry converges on exactly one published version.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import operations, review
from .framemap import FrameMap
from .models import (
    AnnotationSnapshot,
    DatasetVersion,
    LabelingBatch,
    LabelingJob,
    LabelingProject,
    LabelingTask,
    LabelSchemaVersion,
    Operation,
    Outbox,
    ReviewEvent,
    TenantCvatOrg,
)
from .service import Conflict, Ctx, NotFound

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "schemas"
CANONICAL_FORMAT = "labeling.canonical_annotations.v1"
NATIVE_FORMAT, NATIVE_VERSION = "cvat_video_1.1", "1.1"


class NotEligible(Conflict):
    def __init__(self, reasons: list[str]):
        super().__init__("batch is not eligible for send-off: " + "; ".join(reasons))
        self.reasons = reasons


class SendOffAborted(Exception):
    """Non-retryable: the batch was returned to review; nothing was published."""

    retryable = False


def _registry() -> Registry:
    schemas = [json.loads(p.read_text()) for p in CONTRACTS.glob("*.schema.json")]
    return Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)


def _validate(name: str, doc: dict) -> None:
    schema = json.loads((CONTRACTS / name).read_text())
    jsonschema.Draft202012Validator(schema, registry=_registry(), format_checker=jsonschema.FormatChecker()).validate(doc)


def _dump(doc: dict) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def _batch_rows(session: Session, tenant_id: str, batch_id: uuid.UUID):
    return session.execute(
        select(LabelingJob, LabelingTask)
        .join(LabelingTask, (LabelingTask.tenant_id == LabelingJob.tenant_id) & (LabelingTask.id == LabelingJob.task_id))
        .where(LabelingJob.tenant_id == tenant_id, LabelingTask.batch_id == batch_id)
        .order_by(LabelingTask.source_asset_id, LabelingJob.start_frame)).all()


def eligibility(session: Session, ctx: Ctx, tenant_id: str, batch: LabelingBatch) -> list[str]:
    """Reasons the batch cannot be sent off (empty = eligible). Portal-side checks; the barrier re-verifies content."""
    reasons: list[str] = []
    if batch.lifecycle != "created":
        reasons.append(f"batch lifecycle is {batch.lifecycle!r}, expected 'created'")
    rows = _batch_rows(session, tenant_id, batch.id)
    if not rows:
        reasons.append("batch has no jobs")
    project = session.get(LabelingProject, batch.project_id)
    schema_labels = {lb["name"] for lb in session.get(LabelSchemaVersion, project.schema_version_id).content["labels"]}
    for job, task in rows:
        tag = f"job {job.id}"
        if task.status != "ready":
            reasons.append(f"{tag}: task is {task.status}")
        if job.portal_state != "approved" or job.approved_snapshot_id is None:
            reasons.append(f"{tag}: state is {job.portal_state}, not approved")
            continue
        if review.open_blocking_issues(session, job):
            reasons.append(f"{tag}: unresolved blocking issues")
        if not session.scalar(select(ReviewEvent.id).where(ReviewEvent.job_id == job.id, ReviewEvent.decision == "approve",
                                                          ReviewEvent.snapshot_id == job.approved_snapshot_id,
                                                          ReviewEvent.details["attestation"].astext != "")):
            reasons.append(f"{tag}: missing review attestation")
        tele = task.clip.get("telemetry") or {}
        if tele.get("status") != "aligned" and not tele.get("reason"):
            reasons.append(f"{tag}: telemetry is {tele.get('status')!r} with no recorded reason")
        snap = session.get(AnnotationSnapshot, job.approved_snapshot_id)
        doc = json.loads(ctx.store.get(snap.uri))
        unknown = {doc["labels"].get(str(s["label_id"])) for k in ("shapes", "tracks", "tags") for s in doc["annotations"][k]} - schema_labels - {None}
        if unknown:
            reasons.append(f"{tag}: labels outside the schema: {sorted(unknown)}")
    return reasons


def request_send_off(session: Session, ctx: Ctx, *, tenant_id: str, batch_id: uuid.UUID, idempotency_key: str) -> tuple[DatasetVersion, Operation]:
    batch = session.scalar(select(LabelingBatch).where(LabelingBatch.tenant_id == tenant_id, LabelingBatch.id == batch_id).with_for_update())
    if batch is None:
        raise NotFound("batch not found for tenant")
    payload = {"batch_id": str(batch_id)}
    existing = session.scalar(select(Operation).where(Operation.tenant_id == tenant_id, Operation.kind == "send_off",
                                                      Operation.idempotency_key == idempotency_key))
    if existing is None:
        if (reasons := eligibility(session, ctx, tenant_id, batch)):
            raise NotEligible(reasons)
    op, created = operations.enqueue(session, tenant_id=tenant_id, kind="send_off", idempotency_key=idempotency_key, payload=payload, subject_id=batch_id)
    if created:
        dv = session.scalar(select(DatasetVersion).where(DatasetVersion.tenant_id == tenant_id, DatasetVersion.batch_id == batch_id))
        if dv is not None:
            raise Conflict("batch already has a dataset version; start a revision batch to publish changes")
        dv = DatasetVersion(tenant_id=tenant_id, batch_id=batch_id, operation_id=op.id, predecessor_version_id=batch.predecessor_version_id)
        session.add(dv)
        batch.lifecycle = "sending_off"
        session.flush()
    else:
        dv = session.scalar(select(DatasetVersion).where(DatasetVersion.tenant_id == tenant_id, DatasetVersion.batch_id == batch_id))
    return dv, op


def _step(op: Operation, name: str, value=True) -> None:
    op.progress = {**op.progress, name: value}


def _abort(session: Session, ctx: Ctx, op: Operation, batch: LabelingBatch, dv: DatasetVersion, tasks: list[LabelingTask], why: str) -> None:
    """Allowed only because nothing is published: lift the barrier, drop the unpublished version row, return to review."""
    if dv.status == "published":
        raise RuntimeError("cannot abort a published send-off")
    for t in tasks:
        ctx.cvat.unfreeze_task(t.cvat_task_id)
    batch.lifecycle = "created"
    session.delete(dv)
    _step(op, "aborted", why)
    session.commit()
    raise SendOffAborted(why)


def _canonical_annotations(session: Session, ctx: Ctx, dv: DatasetVersion, rows) -> bytes:
    """Per-clip annotations from the *approved snapshots* (not live CVAT), mapped to source time. Jobs are disjoint
    (overlap=0), so each frame is emitted once; a violated invariant fails loudly rather than duplicating annotations."""
    clips: dict[str, dict] = {}
    for job, task in rows:
        snap = session.get(AnnotationSnapshot, job.approved_snapshot_id)
        doc = json.loads(ctx.store.get(snap.uri))
        fmap = FrameMap.from_json(task.frame_map)
        clip = clips.setdefault(task.source_asset_id, {"source_asset_id": task.source_asset_id, "frame_count": fmap.cvat_frame_count, "frames": {}})
        for s in doc["annotations"]["shapes"]:
            f = s["frame"]
            if not job.start_frame <= f <= job.stop_frame:
                raise ValueError(f"shape on frame {f} outside job range")
            clip["frames"].setdefault(f, {"frame": f, "source_time_s": fmap.cvat_to_source_time(f), "shapes": []})["shapes"].append(
                {"label": doc["labels"][str(s["label_id"])], "type": s["type"], "points": s["points"], "occluded": s["occluded"],
                 "attributes": s.get("attributes", []), "source": s.get("source", "manual")})
        for key in ("tracks", "tags"):
            if doc["annotations"][key]:
                clip.setdefault(key, []).extend(doc["annotations"][key])
    out = [{**c, "frames": [c["frames"][k] for k in sorted(c["frames"])]} for c in sorted(clips.values(), key=lambda c: c["source_asset_id"])]
    return _dump({"format": CANONICAL_FORMAT, "dataset_version_id": str(dv.id), "clips": out})


def _put_adopt(ctx: Ctx, key: str, make_bytes) -> tuple[str, str, int]:
    """Write-once with adoption: reuse an object a previous (crashed) attempt already wrote instead of re-exporting."""
    found = ctx.store.exists(key)
    if found:
        uri, sha = found
        return uri, sha, len(ctx.store.get(uri))
    data = make_bytes()
    uri, sha = ctx.store.put_if_absent(key, data)
    return uri, sha, len(data)


def send_off(ctx: Ctx):
    def handler(session: Session, op: Operation) -> None:
        batch = session.scalar(select(LabelingBatch).where(LabelingBatch.id == op.subject_id).with_for_update())
        dv = session.scalar(select(DatasetVersion).where(DatasetVersion.operation_id == op.id))
        if dv is None or dv.status == "published":
            return  # replay of a completed send-off: nothing to do (exactly-once)
        org = session.get(TenantCvatOrg, (batch.tenant_id, ctx.settings.cvat_instance))
        rows = _batch_rows(session, batch.tenant_id, batch.id)
        tasks = list({t.id: t for _, t in rows}.values())
        prefix = f"datasets/{batch.tenant_id}/{batch.id}/{dv.id}"

        if "frozen" not in op.progress:  # 1. write barrier (waits for in-flight writers; rejects every later one)
            for t in tasks:
                ctx.cvat.freeze_task(t.cvat_task_id, str(op.id))
            _step(op, "frozen")
            session.commit()

        if "verified" not in op.progress:  # 2. approved content must still be the live content, checked by hash on the frozen tasks
            stale = [job.id for job, task in rows if not review.verify_approval(session, ctx, job, task, org)]
            if stale:
                _abort(session, ctx, op, batch, dv, tasks, f"approvals no longer match live annotations for jobs {[str(s) for s in stale]}")
            dv.revision_set = {str(j.id): session.get(AnnotationSnapshot, j.approved_snapshot_id).sha256 for j, _ in rows}
            _step(op, "verified")
            session.commit()

        if "artifacts" not in op.progress:  # 3-4. native + canonical exports as write-once objects
            arts = []
            for t in sorted(tasks, key=lambda t: t.source_asset_id):
                uri, sha, n = _put_adopt(ctx, f"{prefix}/native/{t.source_asset_id}.zip",
                                         lambda t=t: ctx.cvat.export_task_native(org.cvat_org_slug, t.cvat_task_id))
                arts.append({"uri": uri, "format": NATIVE_FORMAT, "format_version": NATIVE_VERSION, "sha256": sha, "bytes": n})
            uri, sha, n = _put_adopt(ctx, f"{prefix}/annotations.json", lambda: _canonical_annotations(session, ctx, dv, rows))
            arts.append({"uri": uri, "format": CANONICAL_FORMAT, "format_version": "1", "sha256": sha, "bytes": n})
            _step(op, "artifacts", arts)
            session.commit()

        if "manifest" not in op.progress:
            project = session.get(LabelingProject, batch.project_id)
            schema = session.get(LabelSchemaVersion, project.schema_version_id)
            evidence = []
            for job, _ in rows:
                ev = session.scalar(select(ReviewEvent).where(ReviewEvent.job_id == job.id, ReviewEvent.decision == "approve",
                                                              ReviewEvent.snapshot_id == job.approved_snapshot_id))
                snap = session.get(AnnotationSnapshot, job.approved_snapshot_id)
                evidence.append({"job_id": str(job.id), "annotation_revision": snap.annotation_revision, "snapshot_sha256": snap.sha256,
                                 "reviewer_id": ev.actor_id, "decision": "approved", "decided_at": ev.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                                 "attestation": ev.details.get("attestation", "")})
            manifest = {
                "manifest_version": 1, "tenant_id": batch.tenant_id, "dataset_version_id": str(dv.id), "batch_id": str(batch.id),
                "predecessor_version_id": str(dv.predecessor_version_id) if dv.predecessor_version_id else None,
                "label_schema": {"version": schema.semver, "sha256": schema.content_hash},
                "media": [{**t.clip, "frame_map": t.frame_map} for t in sorted(tasks, key=lambda t: t.source_asset_id)],
                "cvat": {"server_version": ctx.settings.cvat_server_version, "image_digest": ctx.settings.cvat_image_digest, "guard_patch": "0001-write-guard"},
                "model_provenance": None, "review_evidence": evidence, "artifacts": op.progress["artifacts"],
                "created_at": op.progress.get("created_at") or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            _step(op, "created_at", manifest["created_at"])
            _validate("dataset_manifest.schema.json", manifest)
            uri, sha, _ = _put_adopt(ctx, f"{prefix}/manifest.json", lambda: _dump(manifest))
            dv.manifest_uri, dv.manifest_sha256, batch.lifecycle = uri, sha, "publishing"
            _step(op, "manifest")
            session.commit()

        if "object_hashes" not in op.progress:  # 5. verify what is actually in the store before telling anyone it exists
            import hashlib
            for a in op.progress["artifacts"]:
                if hashlib.sha256(ctx.store.get(a["uri"])).hexdigest() != a["sha256"]:
                    raise SendOffAborted(f"stored object {a['uri']} does not match its recorded hash")
            if hashlib.sha256(ctx.store.get(dv.manifest_uri)).hexdigest() != dv.manifest_sha256:
                raise SendOffAborted("stored manifest does not match its recorded hash")
            _step(op, "object_hashes")
            session.commit()

        # 5b. register (idempotent on dataset_version_id; a failure here is retryable and keeps the batch in `publishing`)
        ack = ctx.publisher.register(batch.tenant_id, str(dv.id), dv.manifest_uri, dv.manifest_sha256)
        # 6. durable publication: version, job/batch states and the outbox event commit together
        dv.status, dv.catalog_ack, dv.published_at = "published", ack, datetime.now(UTC)
        for job, _ in rows:
            job.portal_state = "sent_off"
            job.revision += 1
        batch.lifecycle = "sent_off"
        event = {"event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"dataset_version.published:{dv.id}")),
                 "event_type": "labeling.dataset_version.published.v1", "schema_version": 1, "tenant_id": batch.tenant_id,
                 "dataset_version_id": str(dv.id), "batch_id": str(batch.id),
                 "manifest": {"uri": dv.manifest_uri, "version": 1, "sha256": dv.manifest_sha256},
                 "occurred_at": dv.published_at.astimezone(UTC).isoformat().replace("+00:00", "Z"), "correlation_id": str(op.id)}
        _validate("dataset_version_published_v1.schema.json", event)
        session.add(Outbox(tenant_id=batch.tenant_id, aggregate_type="dataset_version", aggregate_id=dv.id,
                           event_type=event["event_type"], payload=event))
        _step(op, "published", str(dv.id))
        session.commit()

    return handler
