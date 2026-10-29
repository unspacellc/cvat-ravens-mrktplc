"""Batch pre-labeling orchestration (Phase 4).

Flow: entitlement + pinned model -> bounded frame chunks -> async inference -> validation/normalization (raw and
normalized stored outside CVAT) -> import ONCE through a ledger -> release the batch for annotation.
Annotator access is gated for the whole run (`gate`), and a run never overwrites a job that already holds annotations
that this run did not produce. Failure, cancellation and "model found nothing" are three different recorded outcomes.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import subprocess
import tempfile
import uuid
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import operations, review, transforms
from .framemap import FrameMap
from .inference import PollResult
from .models import (
    LabelingBatch,
    LabelingJob,
    LabelingProject,
    LabelingTask,
    LabelSchemaVersion,
    Operation,
    PrelabelChunk,
    PrelabelImport,
    PrelabelRun,
    TenantCvatOrg,
)
from .service import Conflict, Ctx, Forbidden, Invalid, NotFound

log = logging.getLogger(__name__)
ACTIVE = ("queued", "running")
TERMINAL = ("succeeded", "failed", "cancelled", "manual_fallback")
SUPPORTED_PROVIDERS = ("sagemaker_async",)  # bedrock_nova / rekognition: descriptor field reserved, adapters not built
DEFAULTS = {"frames_per_request": 8, "frame_stride": 1, "confidence_threshold": 0.25, "max_inflight": 4, "max_attempts": 3, "bounds_tolerance_px": 2.0}


class PrelabelFailed(Exception):
    retryable = False


def gate(session: Session, tenant_id: str, batch_id: uuid.UUID) -> None:
    """Annotators must not be given (or open) work whose pre-labels are still being prepared."""
    if session.scalar(select(PrelabelRun.id).where(PrelabelRun.tenant_id == tenant_id, PrelabelRun.batch_id == batch_id,
                                                   PrelabelRun.status.in_(ACTIVE)).limit(1)):
        raise Conflict("pre-labeling is in progress for this batch; annotation is disabled until import completes or an admin chooses manual labeling")


def create_run(session: Session, ctx: Ctx, *, tenant_id: str, batch_id: uuid.UUID, model_id: str, created_by: str, idempotency_key: str,
               settings: dict | None = None) -> tuple[PrelabelRun, Operation]:
    if ctx.models is None or ctx.inference is None or ctx.store is None:
        raise Invalid("pre-labeling is not configured (model catalog, inference backend and object store are required)")
    cfg = {**DEFAULTS, **(settings or {})}
    payload = {"batch": str(batch_id), "model": model_id, "settings": cfg}
    existing = session.scalar(select(Operation).where(Operation.tenant_id == tenant_id, Operation.kind == "prelabel_run",
                                                      Operation.idempotency_key == idempotency_key))
    if existing is not None:
        op, _ = operations.enqueue(session, tenant_id=tenant_id, kind="prelabel_run", idempotency_key=idempotency_key, payload=payload)
        return session.scalar(select(PrelabelRun).where(PrelabelRun.operation_id == op.id)), op
    batch = session.scalar(select(LabelingBatch).where(LabelingBatch.tenant_id == tenant_id, LabelingBatch.id == batch_id).with_for_update())
    if batch is None:
        raise NotFound("batch not found for tenant")
    if batch.lifecycle != "created":
        raise Conflict(f"batch lifecycle is {batch.lifecycle!r}; pre-labeling needs a provisioned batch")
    if not ctx.models.entitled(tenant_id, model_id):
        raise Forbidden("tenant is not entitled to this model")
    desc = ctx.models.resolve(tenant_id, model_id)
    if desc.get("provider") not in SUPPORTED_PROVIDERS:
        raise Invalid(f"model provider {desc.get('provider')!r} is not supported yet (supported: {', '.join(SUPPORTED_PROVIDERS)})")
    pre = desc.get("preprocessing") or {}
    if pre.get("mode") not in transforms.MODES or (pre["mode"] != "none" and not pre.get("input_size")):
        raise Invalid("model descriptor needs preprocessing.mode and input_size")
    project = session.get(LabelingProject, batch.project_id)
    schema_labels = {lb["name"] for lb in session.get(LabelSchemaVersion, project.schema_version_id).content["labels"]}
    mapping = desc.get("class_mapping") or {}
    if not mapping or set(mapping.values()) - schema_labels:
        raise Invalid(f"class mapping targets labels outside the schema: {sorted(set(mapping.values()) - schema_labels)}")
    if session.scalar(select(PrelabelRun.id).where(PrelabelRun.tenant_id == tenant_id, PrelabelRun.batch_id == batch_id,
                                                   PrelabelRun.status.in_((*ACTIVE, "succeeded"))).limit(1)):
        raise Conflict("this batch already has an active or completed pre-label run; edits after pre-labeling need a revision batch")
    states = set(session.scalars(select(LabelingJob.portal_state).join(
        LabelingTask, (LabelingTask.tenant_id == LabelingJob.tenant_id) & (LabelingTask.id == LabelingJob.task_id)).where(
        LabelingJob.tenant_id == tenant_id, LabelingTask.batch_id == batch_id)))
    if not states or states != {"created"}:
        raise Conflict("pre-labeling is only allowed before annotation starts (all jobs must be untouched)")
    op, created = operations.enqueue(session, tenant_id=tenant_id, kind="prelabel_run", idempotency_key=idempotency_key, payload=payload,
                                     subject_id=uuid.uuid4(), max_attempts=5)
    run = PrelabelRun(id=op.subject_id, tenant_id=tenant_id, batch_id=batch_id, model_id=model_id, model_descriptor=desc, settings=cfg,
                      operation_id=op.id, created_by=created_by, status="queued")
    session.add(run)
    session.flush()
    return run, op


# --- frame preparation -------------------------------------------------------------------------------------------


def _decode_frames(clip: Path, wanted: set[int], n_expected: int) -> dict[int, Image.Image]:
    """Decode a clip to PNGs in presentation order and return the requested clip-frame indexes."""
    with tempfile.TemporaryDirectory(prefix="lb-frames-") as d:
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-fps_mode", "passthrough", "-start_number", "0", f"{d}/%06d.png"],
                       check=True, capture_output=True)
        files = sorted(Path(d).glob("*.png"))
        if len(files) != n_expected:  # alignment hazard: never guess a mapping
            raise PrelabelFailed(f"decoded {len(files)} frames but the frame map has {n_expected}")
        return {i: Image.open(files[i]).copy() for i in sorted(wanted)}


def _chunk_payload(run: PrelabelRun, task: LabelingTask, chunk: PrelabelChunk, images: dict[int, Image.Image], fmap: FrameMap,
                   tf: transforms.Transform) -> bytes:
    frames = []
    for k in chunk.frames:
        src = images[fmap.cvat_to_clip(k)]
        if src.size != (tf.orig_w, tf.orig_h):
            raise PrelabelFailed(f"decoded frame is {src.size}, media reference says {(tf.orig_w, tf.orig_h)}")
        buf = io.BytesIO()
        transforms.apply(src, tf).save(buf, format="PNG")
        raw = buf.getvalue()
        frames.append({"frame_id": f"{task.id}:{k}", "source_time_s": fmap.cvat_to_source_time(k), "sha256": hashlib.sha256(raw).hexdigest(),
                       "width": tf.in_w, "height": tf.in_h, "image_png_b64": base64.b64encode(raw).decode()})
    return json.dumps({"schema": 1, "run_id": str(run.id), "seq": chunk.seq, "frames": frames}, separators=(",", ":")).encode()


def _task_geometry(ctx: Ctx, run: PrelabelRun, task: LabelingTask):
    fmap = FrameMap.from_json(task.frame_map)
    w, h = task.clip["dimensions"]["width"], task.clip["dimensions"]["height"]
    pre = run.model_descriptor["preprocessing"]
    in_w, in_h = (pre.get("input_size") or [w, h])
    return fmap, transforms.make_transform(w, h, in_w, in_h, pre["mode"])


def _ensure_chunks(session: Session, run: PrelabelRun) -> None:
    if session.scalar(select(PrelabelChunk.id).where(PrelabelChunk.run_id == run.id).limit(1)):
        return
    cfg = run.settings
    for task in session.scalars(select(LabelingTask).where(LabelingTask.tenant_id == run.tenant_id, LabelingTask.batch_id == run.batch_id)
                                .order_by(LabelingTask.source_asset_id)):
        if task.status != "ready":
            raise PrelabelFailed(f"task {task.id} is {task.status}, not ready")
        fmap = FrameMap.from_json(task.frame_map)
        frames = [k for k in range(fmap.cvat_frame_count) if k % cfg["frame_stride"] == 0]
        for seq, i in enumerate(range(0, len(frames), cfg["frames_per_request"])):
            c = PrelabelChunk(tenant_id=run.tenant_id, run_id=run.id, task_id=task.id, seq=seq, frames=frames[i:i + cfg["frames_per_request"]], inference_id="pending")
            session.add(c)
            session.flush()
            c.inference_id = _inference_id(run, c)
    session.commit()


def _inference_id(run: PrelabelRun, c: PrelabelChunk) -> str:
    """Deterministic per (chunk, attempt): an ambiguous submit timeout re-sends the same id; a real retry gets a new one."""
    epoch = run.summary.get("epoch", 0)  # bumped by retry_run so a manual retry never reuses a failed inference id
    return hashlib.sha256(f"{run.id}:{c.id}:{epoch}:{c.attempts}".encode()).hexdigest()[:40]


# --- validation / normalization ----------------------------------------------------------------------------------


def normalize(output: bytes, chunk: PrelabelChunk, task: LabelingTask, tf: transforms.Transform, mapping: dict[str, str], cfg: dict) -> tuple[list[dict], dict]:
    """Strict: a malformed or incomplete response raises ValueError (=> failed attempt, never an 'empty' result)."""
    doc = json.loads(output)
    if doc.get("schema") != 1 or not isinstance(doc.get("frames"), list):
        raise ValueError("unsupported response schema")
    expected = {f"{task.id}:{k}" for k in chunk.frames}
    got = [f.get("frame_id") for f in doc["frames"]]
    if len(got) != len(set(got)) or set(got) != expected:
        raise ValueError(f"response frames do not match request (missing {sorted(expected - set(got))[:3]}, unexpected {sorted(set(got) - expected)[:3]})")
    counters = {"dropped_unknown_label": 0, "dropped_low_confidence": 0, "dropped_invalid": 0}
    out, tol = [], cfg["bounds_tolerance_px"]
    for f in doc["frames"]:
        k = int(f["frame_id"].rsplit(":", 1)[1])
        for d in f.get("detections") or []:
            score, box = d.get("score"), d.get("box")
            if not transforms.finite_box(box) or not isinstance(score, (int, float)) or not 0 <= score <= 1 or box[2] <= box[0] or box[3] <= box[1]:
                counters["dropped_invalid"] += 1
                continue
            if d.get("label") not in mapping:
                counters["dropped_unknown_label"] += 1
                continue
            if score < cfg["confidence_threshold"]:
                counters["dropped_low_confidence"] += 1
                continue
            x1, y1, x2, y2 = tf.to_orig(box)
            if x1 < -tol or y1 < -tol or x2 > tf.orig_w + tol or y2 > tf.orig_h + tol:
                counters["dropped_invalid"] += 1  # outside the frame after the inverse transform => wrong mapping, not a detection
                continue
            x1, y1, x2, y2 = max(0.0, x1), max(0.0, y1), min(float(tf.orig_w), x2), min(float(tf.orig_h), y2)
            if x2 <= x1 or y2 <= y1:
                counters["dropped_invalid"] += 1
                continue
            out.append({"frame": k, "label": mapping[d["label"]], "model_label": d["label"], "score": float(score), "box": [x1, y1, x2, y2]})
    return out, counters


# --- tick --------------------------------------------------------------------------------------------------------


def _put(ctx: Ctx, key: str, data: bytes) -> str:
    uri, _ = ctx.store.put_if_absent(key, data)
    return uri


def _advance(session: Session, ctx: Ctx, run: PrelabelRun) -> bool:
    """One pass of collect + submit. Returns True when nothing is pending or in flight."""
    cfg, backend = run.settings, ctx.inference
    tasks = {t.id: t for t in session.scalars(select(LabelingTask).where(LabelingTask.tenant_id == run.tenant_id, LabelingTask.batch_id == run.batch_id))}
    geo = {tid: _task_geometry(ctx, run, t) for tid, t in tasks.items()}
    chunks = list(session.scalars(select(PrelabelChunk).where(PrelabelChunk.run_id == run.id).order_by(PrelabelChunk.task_id, PrelabelChunk.seq)))
    counters = dict(run.summary.get("counters", {"dropped_unknown_label": 0, "dropped_low_confidence": 0, "dropped_invalid": 0}))

    for c in (c for c in chunks if c.status == "submitted"):  # collect
        res: PollResult = backend.poll(c.inference_id)
        if res.state == "pending":
            continue
        task, (fmap, tf) = tasks[c.task_id], geo[c.task_id]
        try:
            if res.state == "failed":
                raise ValueError(res.error or "inference failed")
            preds, ctr = normalize(res.output, c, task, tf, run.model_descriptor["class_mapping"], cfg)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            c.attempts += 1
            c.error = str(e)[:500]
            c.status = "pending" if c.attempts < cfg["max_attempts"] else "failed"
            if c.status == "pending":
                c.inference_id = _inference_id(run, c)
            continue
        c.raw_uri = _put(ctx, f"prelabel/{run.tenant_id}/{run.id}/raw/{c.id}-{c.attempts}.json", res.output)
        c.normalized_uri = _put(ctx, f"prelabel/{run.tenant_id}/{run.id}/normalized/{c.id}.json", json.dumps(preds, sort_keys=True).encode())
        for k, v in ctr.items():
            counters[k] += v
        c.status, c.error = "collected", None
    run.summary = {**run.summary, "counters": counters}
    session.commit()

    inflight = sum(1 for c in chunks if c.status == "submitted")
    todo = [c for c in chunks if c.status == "pending"]
    cache: dict[uuid.UUID, dict[int, Image.Image]] = {}
    for c in todo:
        if inflight >= cfg["max_inflight"]:
            break
        task, (fmap, tf) = tasks[c.task_id], geo[c.task_id]
        if c.task_id not in cache:
            clip = ctx.clips.local_path(task.clip)
            wanted = {fmap.cvat_to_clip(k) for ch in chunks if ch.task_id == c.task_id and ch.status == "pending" for k in ch.frames}
            cache[c.task_id] = _decode_frames(clip, wanted, len(fmap.pts_s))
        payload = _chunk_payload(run, task, c, cache[c.task_id], fmap, tf)
        c.input_uri = _put(ctx, f"prelabel/{run.tenant_id}/{run.id}/in/{c.id}-{c.attempts}.json", payload)
        backend.submit(inference_id=c.inference_id, payload=payload)  # raises on transport errors => op retries; same id => no duplicate
        c.status = "submitted"
        inflight += 1
        session.commit()
    return not any(c.status in ("pending", "submitted") for c in chunks)


def _shape(label_id: int, p: dict, source: str = "auto") -> dict:
    return {"type": "rectangle", "frame": p["frame"], "label_id": label_id, "points": [round(v, 4) for v in p["box"]], "occluded": False,
            "outside": False, "z_order": 0, "rotation": 0, "attributes": [], "source": source}


def _same_shapes(live: list[dict], expected: list[dict]) -> bool:
    def key(s):
        return (s["frame"], s["label_id"], s["type"], tuple(round(v, 3) for v in s["points"]))
    return sorted(map(key, live)) == sorted(map(key, expected))


def _import_task(session: Session, ctx: Ctx, run: PrelabelRun, task: LabelingTask, org: TenantCvatOrg) -> dict:
    ledger = session.get(PrelabelImport, (run.tenant_id, run.id, task.id)) or PrelabelImport(tenant_id=run.tenant_id, run_id=run.id, task_id=task.id)
    session.add(ledger)
    if ledger.status in ("done", "empty"):
        return {"status": ledger.status, "shapes": ledger.shapes_expected}
    preds = []
    for c in session.scalars(select(PrelabelChunk).where(PrelabelChunk.run_id == run.id, PrelabelChunk.task_id == task.id).order_by(PrelabelChunk.seq)):
        preds += json.loads(ctx.store.get(c.normalized_uri))
    ledger.shapes_expected = len(preds)
    if not preds:  # the model ran and found nothing: a real, distinct outcome
        ledger.status = "empty"
        session.commit()
        return {"status": "empty", "shapes": 0}
    baseline = json.dumps(preds, sort_keys=True).encode()
    ledger.baseline_uri, ledger.baseline_sha256 = _put(ctx, f"prelabel/{run.tenant_id}/{run.id}/baseline/{task.id}.json", baseline), hashlib.sha256(baseline).hexdigest()
    names = {v: k for k, v in ctx.cvat.task_labels(org.cvat_org_slug, task.cvat_task_id).items()}
    imported = 0
    for job in session.scalars(select(LabelingJob).where(LabelingJob.tenant_id == run.tenant_id, LabelingJob.task_id == task.id).order_by(LabelingJob.start_frame)):
        jp = [p for p in preds if job.start_frame <= p["frame"] <= job.stop_frame]
        want = [_shape(names[p["label"]], p) for p in jp]
        if str(job.id) in ledger.jobs:  # already imported by an earlier attempt: never again
            imported += len(want)
            continue
        live = ctx.cvat.get_job_annotations(org.cvat_org_slug, job.cvat_job_id)
        has_any = bool(live.get("shapes") or live.get("tags") or live.get("tracks"))
        if has_any and not (not live.get("tags") and not live.get("tracks") and _same_shapes(live["shapes"], want)):
            ledger.status, ledger.error = "conflict", f"job {job.id} holds annotations this run did not produce; refusing to overwrite"
            session.commit()
            return {"status": "conflict", "shapes": imported}
        if not has_any and want:
            ctx.cvat.put_job_annotations(org.cvat_org_slug, job.cvat_job_id, {"version": 0, "tags": [], "tracks": [], "shapes": want})
            live = ctx.cvat.get_job_annotations(org.cvat_org_slug, job.cvat_job_id)
            if not _same_shapes(live["shapes"], want):  # frame/coordinate alignment verification before release
                ledger.status, ledger.error = "conflict", f"job {job.id}: imported shapes do not read back as sent"
                session.commit()
                raise PrelabelFailed(ledger.error)
        _, sha = review.canonical_snapshot(ctx, org.cvat_org_slug, job, task)
        ledger.jobs = {**ledger.jobs, str(job.id): sha}
        imported += len(want)
        session.commit()
    ledger.status = "done"
    session.commit()
    return {"status": "done", "shapes": imported}


def _finish(session: Session, ctx: Ctx, run: PrelabelRun, op: Operation) -> None:
    chunks = list(session.scalars(select(PrelabelChunk).where(PrelabelChunk.run_id == run.id)))
    failed = [c for c in chunks if c.status == "failed"]
    if failed:
        run.status, run.error = "failed", f"{len(failed)} of {len(chunks)} inference request(s) failed: {failed[0].error}"
        run.summary = {**run.summary, "failed_chunks": len(failed), "chunks": len(chunks)}
        session.commit()
        raise PrelabelFailed(run.error)
    org = session.get(TenantCvatOrg, (run.tenant_id, ctx.settings.cvat_instance))
    results = {}
    for task in session.scalars(select(LabelingTask).where(LabelingTask.tenant_id == run.tenant_id, LabelingTask.batch_id == run.batch_id).order_by(LabelingTask.source_asset_id)):
        results[str(task.id)] = _import_task(session, ctx, run, task, org)
    run.summary = {**run.summary, "tasks": results, "chunks": len(chunks), "detections_imported": sum(r["shapes"] for r in results.values()),
                   "zero_detection": all(r["status"] == "empty" for r in results.values())}
    if any(r["status"] == "conflict" for r in results.values()):
        run.status, run.error = "failed", "import refused: a job already holds annotations from another source"
        session.commit()
        raise PrelabelFailed(run.error)
    run.status = "succeeded"
    session.commit()


def prelabel_run(ctx: Ctx):
    def handler(session: Session, op: Operation) -> None:
        run = session.scalar(select(PrelabelRun).where(PrelabelRun.id == op.subject_id).with_for_update())
        if run is None or run.status in TERMINAL:
            return  # cancelled / fallen back / already done: late work is discarded
        run.status = "running"
        session.commit()
        _ensure_chunks(session, run)
        if _advance(session, ctx, run):
            _finish(session, ctx, run, op)
            return
        raise operations.Pending(wait_s=1.0)

    return handler


# --- commands ----------------------------------------------------------------------------------------------------


def _stop(session: Session, ctx: Ctx, run: PrelabelRun, status: str) -> None:
    for c in session.scalars(select(PrelabelChunk).where(PrelabelChunk.run_id == run.id, PrelabelChunk.status.in_(("pending", "submitted")))):
        if c.status == "submitted":
            ctx.inference.cancel(c.inference_id)
        c.status, c.error = "failed", status
    run.status = status
    op = session.get(Operation, run.operation_id)
    if op and op.state not in operations.TERMINAL:
        op.state, op.lease_until = "cancelled", None
    session.flush()


def cancel_run(session: Session, ctx: Ctx, *, tenant_id: str, run_id: uuid.UUID) -> PrelabelRun:
    run = _get(session, tenant_id, run_id)
    if run.status not in ACTIVE:
        raise Conflict(f"run is {run.status}; only queued or running runs can be cancelled")
    run.cancel_requested = True
    _stop(session, ctx, run, "cancelled")
    return run


def manual_fallback(session: Session, ctx: Ctx, *, tenant_id: str, run_id: uuid.UUID) -> PrelabelRun:
    """Explicit admin choice to label by hand: stops the run and re-enables annotation. Nothing is imported."""
    run = _get(session, tenant_id, run_id)
    if run.status not in (*ACTIVE, "failed", "cancelled"):
        raise Conflict(f"run is {run.status}; manual fallback applies to active, failed or cancelled runs")
    if run.status in ACTIVE:
        _stop(session, ctx, run, "manual_fallback")
    else:
        run.status = "manual_fallback"
    return run


def retry_run(session: Session, ctx: Ctx, *, tenant_id: str, run_id: uuid.UUID) -> PrelabelRun:
    run = _get(session, tenant_id, run_id)
    if run.status != "failed":
        raise Conflict("only failed runs can be retried")
    run.summary = {**run.summary, "epoch": run.summary.get("epoch", 0) + 1}
    for c in session.scalars(select(PrelabelChunk).where(PrelabelChunk.run_id == run.id, PrelabelChunk.status == "failed")):
        c.status, c.attempts, c.error = "pending", 0, None
        c.inference_id = _inference_id(run, c)
    op = session.get(Operation, run.operation_id)
    operations.retry(op)
    run.status, run.error = "queued", None
    session.flush()
    return run


def _get(session: Session, tenant_id: str, run_id: uuid.UUID) -> PrelabelRun:
    run = session.scalar(select(PrelabelRun).where(PrelabelRun.tenant_id == tenant_id, PrelabelRun.id == run_id).with_for_update())
    if run is None:
        raise NotFound("pre-label run not found for tenant")
    return run
