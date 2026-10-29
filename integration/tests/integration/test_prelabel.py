"""Phase 4 on real CVAT with a deterministic offline 'endpoint' that detects the box in the preprocessed pixels."""

import uuid

import pytest
from sqlalchemy import select

from api.labeling import fixtures as fx
from api.labeling import operations, prelabel, service
from api.labeling.framemap import FrameMap
from api.labeling.inference import FakeBackend
from api.labeling.models import LabelingJob, LabelingTask, Operation, PrelabelChunk, PrelabelImport, PrelabelRun

from .conftest import FIXTURES
from .pixel_clip import ambiguous, expected_box, make_pixel_clip
from .review_harness import make_setup

pytestmark = pytest.mark.capability
OFFSET = 12.5
DESC = {"provider": "sagemaker_async", "model_id": "vehicle-det", "artifact": {"uri": "s3://models/vehicle-det-1.0.tar.gz", "digest": "sha256:" + "a" * 64, "version": "1.0.0"},
        "class_mapping": {"vehicle": "car"}, "preprocessing": {"mode": "letterbox", "input_size": [256, 256]}}


@pytest.fixture
def pl(session_factory, cvat_token, clips):
    clips = {**clips, "pixel": make_pixel_clip(FIXTURES)}
    t = f"tenant-pl-{uuid.uuid4().hex[:8]}"
    asset = f"a-{t}"
    # the harness builds the media ref from a clip name; override with the offset ref
    st = make_setup(session_factory, cvat_token, {**clips, "px": clips["pixel"]}, t, clip="px", segment_size=40,
                    extra_media={(t, asset): fx.make_media_ref(clips["pixel"], asset, root=FIXTURES, clip_start_offset_s=OFFSET)})
    st.ctx.media = fx.FixtureMedia({(t, asset): fx.make_media_ref(clips["pixel"], asset, root=FIXTURES, clip_start_offset_s=OFFSET)})
    st.backend = FakeBackend()
    st.ctx.inference = st.backend
    st.ctx.models = fx.FixtureModels({t: {"m1": DESC}})
    return st


def start(st, key="p1", model="m1", **settings):
    with st.sf() as s:
        run, op = prelabel.create_run(s, st.ctx, tenant_id=st.tenant, batch_id=st.batch_id, model_id=model, created_by="adm", idempotency_key=key, settings=settings)
        s.commit()
        return run.id, op.id


def drive(st, op_id, max_ticks=400):
    h = {"prelabel_run": prelabel.prelabel_run(st.ctx)}
    for _ in range(max_ticks):
        operations.run_one(st.sf, h, op_id=op_id)
        with st.sf() as s:
            op = s.get(Operation, op_id)
            if op.state in operations.TERMINAL:
                return op.state
            if op.state in ("queued", "retrying"):
                op.run_after = operations.now()
                s.commit()
    raise AssertionError("operation did not finish")


def cvat_shapes(st, i):
    with st.sf() as s:
        job = st.job(s, i)
        return job, st.ctx.cvat.get_job_annotations(st.slug, job.cvat_job_id)["shapes"]


def expected_for_job(st, job, stride=1):
    out = {}
    for k in range(job.start_frame, job.stop_frame + 1):
        t = k / 30 if k < 60 else 2 + (k - 60) / 10  # the generator's own content clock (independent of container timestamps)
        if k % stride or ambiguous(t):
            continue
        out[k] = expected_box(t)
    return out


def test_prelabels_align_with_pixels_through_letterbox_offset_and_vfr(pl):
    st = pl
    st.backend.extra = [{"label": "bicycle", "score": 0.8, "box": [10, 10, 50, 50]}]  # unknown to the class mapping
    run_id, op_id = start(st)
    assert drive(st, op_id) == "succeeded"
    # the pinned input really was letterboxed 320x240 -> 256x256 and carried offset-aware source times
    import json as _json

    from jsonschema import Draft202012Validator

    from api.labeling.sendoff import CONTRACTS
    req = Draft202012Validator(_json.loads((CONTRACTS / "prelabel_request.schema.json").read_text()))
    resp = Draft202012Validator(_json.loads((CONTRACTS / "prelabel_response.schema.json").read_text()))
    for payload in st.backend.payloads.values():
        req.validate(payload)
        resp.validate(st.backend._detect(payload))  # the wire formats are the documented contract, not an accident of the fake
    any_payload = next(iter(st.backend.payloads.values()))
    assert {(f["width"], f["height"]) for f in any_payload["frames"]} == {(256, 256)}
    with st.sf() as s:
        task = s.scalars(select(LabelingTask).where(LabelingTask.tenant_id == st.tenant)).one()
        fmap = FrameMap.from_json(task.frame_map)
        assert fmap.variable_frame_rate and fmap.pts_s[0] == pytest.approx(1.5)
    for p in st.backend.payloads.values():
        for f in p["frames"]:
            k = int(f["frame_id"].rsplit(":", 1)[1])
            assert f["source_time_s"] == pytest.approx(OFFSET + fmap.pts_s[fmap.cvat_to_clip(k)])
    checked = empties = 0
    for i in range(len(st.job_ids)):
        job, shapes = cvat_shapes(st, i)
        by_frame = {s["frame"]: s for s in shapes}
        assert len(by_frame) == len(shapes)  # one box per frame, no duplicates
        for k, box in expected_for_job(st, job).items():
            if box is None:
                assert k not in by_frame, f"frame {k} should be empty"
                empties += 1
            else:
                got = by_frame[k]
                assert got["source"] == "auto" and got["type"] == "rectangle"
                assert got["points"] == pytest.approx(box, abs=2.5), f"frame {k}"
                checked += 1
    assert checked > 50 and empties >= 5
    with st.sf() as s:
        run = s.get(PrelabelRun, run_id)
        assert run.status == "succeeded" and run.summary["counters"]["dropped_unknown_label"] == len(fmap.pts_s)
        assert run.summary["zero_detection"] is False and run.summary["detections_imported"] >= checked
        assert s.get(PrelabelImport, (st.tenant, run_id, task.id)).status == "done"
    # raw and normalized predictions are kept outside CVAT
    with st.sf() as s:
        c = s.scalars(select(PrelabelChunk).where(PrelabelChunk.run_id == run_id)).first()
        assert st.ctx.store.get(c.raw_uri) and st.ctx.store.get(c.normalized_uri)


def test_sampled_frames_only_predict_every_nth_frame(pl):
    run_id, op_id = start(pl, "s", frame_stride=3)
    assert drive(pl, op_id) == "succeeded"
    seen = set()
    for i in range(len(pl.job_ids)):
        job, shapes = cvat_shapes(pl, i)
        seen |= {s["frame"] for s in shapes}
    assert seen and all(f % 3 == 0 for f in seen)


def test_annotation_is_gated_while_running_and_released_after(pl):
    st = pl
    run_id, op_id = start(st)
    with st.sf() as s, pytest.raises(service.Conflict, match="pre-labeling is in progress"):
        service.assign_job(s, st.ctx, tenant_id=st.tenant, job_id=st.job_ids[0], user_id="u", username="u-gate")
    assert drive(st, op_id) == "succeeded"
    with st.sf() as s:
        service.assign_job(s, st.ctx, tenant_id=st.tenant, job_id=st.job_ids[0], user_id=f"lab-{st.tenant}",
                           username=service.cvat_username_for(f"lab-{st.tenant}"), uid=f"lab-{st.tenant}")
        s.commit()
    with st.sf() as s, pytest.raises(service.Conflict, match="already has"):  # a second run needs a revision batch
        prelabel.create_run(s, st.ctx, tenant_id=st.tenant, batch_id=st.batch_id, model_id="m1", created_by="a", idempotency_key="again")


def test_replay_never_duplicates_and_never_erases_human_edits(pl):
    st = pl
    run_id, op_id = start(st)
    assert drive(st, op_id) == "succeeded"
    _, before = cvat_shapes(st, 0)
    # a duplicate delivery of the same operation is a no-op
    assert not operations.run_one(st.sf, {"prelabel_run": prelabel.prelabel_run(st.ctx)}, op_id=op_id)
    # ambiguous import timeout: the CVAT write landed but the ledger did not -> adopt, do not append
    with st.sf() as s:
        run = s.get(PrelabelRun, run_id)
        s.query(PrelabelImport).filter_by(run_id=run_id).delete()
        run.status = "running"
        op = s.get(Operation, op_id)
        op.state = "queued"
        s.commit()
    assert drive(st, op_id) == "succeeded"
    _, after = cvat_shapes(st, 0)
    assert len(after) == len(before)
    # human edit, then a lost ledger: the run must refuse to overwrite
    job, shapes = cvat_shapes(st, 0)
    edited = [{**shapes[0], "points": [1.0, 1.0, 20.0, 20.0]}, *shapes[1:]]
    st.ctx.cvat.put_job_annotations(st.slug, job.cvat_job_id, {"version": 0, "tags": [], "tracks": [], "shapes": [{k: v for k, v in s.items() if k != "id"} for s in edited]})
    with st.sf() as s:
        s.query(PrelabelImport).filter_by(run_id=run_id).delete()
        s.get(PrelabelRun, run_id).status = "running"
        s.get(Operation, op_id).state = "queued"
        s.commit()
    assert drive(st, op_id) == "failed"
    _, kept = cvat_shapes(st, 0)
    assert kept[0]["points"] == pytest.approx([1.0, 1.0, 20.0, 20.0])
    with st.sf() as s:
        run = s.get(PrelabelRun, run_id)
        assert run.status == "failed" and "another source" in run.error


def test_zero_detections_failure_and_cancellation_are_distinguishable(pl):
    st = pl
    st.backend.detect = False
    rid_zero, op_zero = start(st, "z")
    assert drive(st, op_zero) == "succeeded"
    with st.sf() as s:
        z = s.get(PrelabelRun, rid_zero)
        assert z.status == "succeeded" and z.summary["zero_detection"] is True and z.summary["detections_imported"] == 0
        assert {i.status for i in s.scalars(select(PrelabelImport).where(PrelabelImport.run_id == rid_zero))} == {"empty"}
    assert cvat_shapes(st, 0)[1] == []
    # a second run is not allowed after success, so use a fresh batch for the failing / cancelled cases
    with st.sf() as s:
        s.get(PrelabelRun, rid_zero).status = "cancelled"  # test shortcut: free the batch for the next scenario
        s.commit()

    st.backend.detect, st.backend.fail_all = True, True
    rid_fail, op_fail = start(st, "f", max_attempts=2)
    assert drive(st, op_fail) == "failed"
    with st.sf() as s:
        f = s.get(PrelabelRun, rid_fail)
        assert f.status == "failed" and "inference request(s) failed" in f.error and "detections_imported" not in f.summary
        assert {c.status for c in s.scalars(select(PrelabelChunk).where(PrelabelChunk.run_id == rid_fail))} == {"failed"}
    assert cvat_shapes(st, 0)[1] == []
    # annotation stays blocked? No: a failed run is not active, so an admin can proceed manually; retry recovers too
    st.backend.fail_all = False
    with st.sf() as s:
        prelabel.retry_run(s, st.ctx, tenant_id=st.tenant, run_id=rid_fail)
        s.commit()
    assert drive(st, op_fail) == "succeeded"
    assert len(cvat_shapes(st, 0)[1]) > 0

    # cancellation mid-flight: nothing is imported, late results are discarded
    with st.sf() as s:
        s.get(PrelabelRun, rid_fail).status = "cancelled"
        s.query(PrelabelImport).filter_by(run_id=rid_fail).delete()
        job = st.job(s, 0)
        st.ctx.cvat.put_job_annotations(st.slug, job.cvat_job_id, {"version": 0, "tags": [], "tracks": [], "shapes": []})
        st.ctx.cvat.put_job_annotations(st.slug, st.job(s, 1).cvat_job_id, {"version": 0, "tags": [], "tracks": [], "shapes": []})
        s.commit()
    rid_c, op_c = start(st, "c")
    h = {"prelabel_run": prelabel.prelabel_run(st.ctx)}
    operations.run_one(st.sf, h, op_id=op_c)  # one tick: chunks submitted
    assert st.backend.submissions
    with st.sf() as s:
        prelabel.cancel_run(s, st.ctx, tenant_id=st.tenant, run_id=rid_c)
        s.commit()
    assert st.backend.cancelled
    assert not operations.run_one(st.sf, h, op_id=op_c)
    assert cvat_shapes(st, 0)[1] == [] and cvat_shapes(st, 1)[1] == []
    with st.sf() as s:
        assert s.get(PrelabelRun, rid_c).status == "cancelled" and s.get(Operation, op_c).state == "cancelled"
        service.assign_job(s, st.ctx, tenant_id=st.tenant, job_id=st.job_ids[0], user_id=f"lab-{st.tenant}",
                           username=service.cvat_username_for(f"lab-{st.tenant}"), uid=f"lab-{st.tenant}")  # gate released


def test_transient_errors_retry_and_malformed_responses_are_failures_not_empties(pl):
    st = pl
    st.backend.transient_failures = 2
    rid, op = start(st, "t", max_attempts=3)
    assert drive(st, op) == "succeeded"
    with st.sf() as s:
        assert sum(c.attempts for c in s.scalars(select(PrelabelChunk).where(PrelabelChunk.run_id == rid))) == 2
        s.get(PrelabelRun, rid).status = "cancelled"
        s.commit()
    st.backend.corrupt = True
    rid2, op2 = start(st, "m", max_attempts=2)
    assert drive(st, op2) == "failed"
    with st.sf() as s:
        run = s.get(PrelabelRun, rid2)
        assert run.status == "failed" and "detections_imported" not in run.summary
        assert "do not match request" in (s.scalars(select(PrelabelChunk).where(PrelabelChunk.run_id == rid2)).first().error or "")


def test_inflight_requests_are_bounded(pl):
    st = pl
    rid, op = start(st, "b", frames_per_request=4, max_inflight=2)
    assert drive(st, op) == "succeeded"
    assert len(st.backend.submissions) == 20 and st.backend.max_inflight_seen <= 2  # 80 frames / 4 per request


def test_manual_fallback_stops_the_run_and_unblocks_annotation(pl):
    st = pl
    rid, op = start(st, "mf")
    operations.run_one(st.sf, {"prelabel_run": prelabel.prelabel_run(st.ctx)}, op_id=op)
    with st.sf() as s:
        run = prelabel.manual_fallback(s, st.ctx, tenant_id=st.tenant, run_id=rid)
        s.commit()
        assert run.status == "manual_fallback"
    assert cvat_shapes(st, 0)[1] == []
    with st.sf() as s:
        service.assign_job(s, st.ctx, tenant_id=st.tenant, job_id=st.job_ids[0], user_id=f"lab-{st.tenant}",
                           username=service.cvat_username_for(f"lab-{st.tenant}"), uid=f"lab-{st.tenant}")


def test_entitlement_provider_and_mapping_are_checked_up_front(pl):
    st = pl
    with st.sf() as s, pytest.raises(service.Forbidden):
        prelabel.create_run(s, st.ctx, tenant_id=st.tenant, batch_id=st.batch_id, model_id="not-mine", created_by="a", idempotency_key="e1")
    st.ctx.models = fx.FixtureModels({st.tenant: {
        "nova": {**DESC, "provider": "bedrock_nova"},
        "bad-map": {**DESC, "class_mapping": {"vehicle": "spaceship"}},
        "no-pre": {**DESC, "preprocessing": {"mode": "mystery"}}}})
    for model, msg in (("nova", "not supported yet"), ("bad-map", "outside the schema"), ("no-pre", "preprocessing")):
        with st.sf() as s, pytest.raises(service.Invalid, match=msg):
            prelabel.create_run(s, st.ctx, tenant_id=st.tenant, batch_id=st.batch_id, model_id=model, created_by="a", idempotency_key=f"e-{model}")
    with st.sf() as s:  # idempotency: same key + same input returns the same run
        st.ctx.models = fx.FixtureModels({st.tenant: {"m1": DESC}})
        r1, o1 = prelabel.create_run(s, st.ctx, tenant_id=st.tenant, batch_id=st.batch_id, model_id="m1", created_by="a", idempotency_key="same")
        s.commit()
        r2, o2 = prelabel.create_run(s, st.ctx, tenant_id=st.tenant, batch_id=st.batch_id, model_id="m1", created_by="a", idempotency_key="same")
        assert (r1.id, o1.id) == (r2.id, o2.id)
        assert s.scalar(select(LabelingJob.id).where(LabelingJob.tenant_id == st.tenant).limit(1)) is not None


def test_correction_metrics_measure_human_changes_against_the_baseline(pl):
    from api.labeling import metrics, review
    st = pl
    rid, op = start(st, "mx")
    assert drive(st, op) == "succeeded"
    lab, rev = f"lab-{st.tenant}", f"rev-{st.tenant}"
    with st.sf() as s:
        service.assign_job(s, st.ctx, tenant_id=st.tenant, job_id=st.job_ids[0], user_id=lab, username=service.cvat_username_for(lab), uid=lab)
        s.commit()
    job, shapes = cvat_shapes(st, 0)
    n = len(shapes)
    lid = shapes[0]["label_id"]
    clean = [{k: v for k, v in x.items() if k != "id"} for x in shapes]
    nudged = {**clean[1], "points": [clean[1]["points"][0] + 20, *clean[1]["points"][1:]]}  # edited
    final = [clean[0], nudged, *clean[3:]]  # clean[2] deleted
    final.append({**clean[0], "frame": 38, "points": [250.0, 150.0, 300.0, 200.0], "source": "manual", "label_id": lid})  # added
    st.ctx.cvat.put_job_annotations(st.slug, job.cvat_job_id, {"version": 0, "tags": [], "tracks": [], "shapes": final})
    actor = st.actor(lab, "labeler")
    with st.sf() as s:
        review.transition(s, st.ctx, actor=actor, job_id=job.id, cmd=review.Command("submit", s.get(LabelingJob, job.id).revision, reviewer_id=rev))
        s.commit()
        sha = review.live_review_sha(st.ctx, s, st.tenant, job.id)
    with st.sf() as s:
        review.transition(s, st.ctx, actor=st.actor(rev, "reviewer"), job_id=job.id,
                          cmd=review.Command("approve", s.get(LabelingJob, job.id).revision, attestation="frames 0-39 reviewed", expected_snapshot_sha256=sha))
        s.commit()
    with st.sf() as s:
        m = metrics.run_metrics(s, st.ctx, tenant_id=st.tenant, run_id=rid)
    j = next(x for x in m["jobs"] if x["job_id"] == str(job.id))
    other = next(x for x in m["jobs"] if x["job_id"] != str(job.id))
    assert other["status"] == "awaiting_approval"  # unreviewed work is not counted
    assert j["status"] == "measured" and j["baseline"] == n
    assert (j["edited"], j["deleted"], j["added"], j["unchanged"]) == (1, 1, 1, n - 2)
    assert j["correction_rate"] == pytest.approx(2 / n) and j["attestation"] == "frames 0-39 reviewed"
    assert m["method"]["version"] == 1 and m["limitations"] and m["jobs_measured"] == 1


def test_offline_resource_measurements(pl, capsys):
    """Records (not just asserts) what a run costs in our own resources; AWS-side cost needs a real endpoint."""
    import time
    st = pl
    t0 = time.time()
    rid, op = start(st, "res", frames_per_request=16)
    assert drive(st, op) == "succeeded"
    wall = time.time() - t0
    sizes = [len(__import__("json").dumps(p)) for p in st.backend.payloads.values()]
    frames = sum(len(p["frames"]) for p in st.backend.payloads.values())
    per_frame = sum(sizes) / frames
    print(f"MEASURED frames={frames} requests={len(sizes)} input_bytes_total={sum(sizes)} input_bytes_per_frame={per_frame:.0f} wall_s={wall:.1f}")
    assert per_frame < 150_000  # 256x256 PNG + base64; a regression here multiplies S3/transfer cost
