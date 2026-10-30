"""Phase 5 creation workflow on the portal API, real CVAT behind it: media/schema selection, split preview, persisted batch
settings, partial-provisioning recovery, operational metrics."""

import shutil
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from api.labeling import fixtures as fx
from api.labeling.models import LabelingBatch, LabelingJob, Operation, ReviewEvent

from .conftest import FIXTURES, build_ctx
from .portal import make_portal

pytestmark = pytest.mark.capability


@pytest.fixture
def world(session_factory, cvat_token, clips, tenant_ids):
    ta, tb = tenant_ids
    u = uuid.uuid4().hex[:6]
    assets = {"a1": (ta, "p1_c"), "a2": (ta, "p1_a"), "a3": (ta, "p1_b"), "b1": (tb, "p1_c")}
    media = {(t, f"{k}-{t}"): fx.make_media_ref(clips[c], f"{k}-{t}", root=FIXTURES) for k, (t, c) in assets.items()}
    ctx = build_ctx(cvat_token, media)
    ctx.settings = replace(ctx.settings, segment_size=40)
    portal = make_portal(session_factory, ctx)
    w = {"portal": portal, "ta": ta, "tb": tb, "admin": f"admin-{u}", "lab": f"lab-{u}", "admin_b": f"adminb-{u}", "media": media,
         "ids": {k: f"{k}-{t}" for k, (t, _) in assets.items()}}
    portal.add_member(ta, w["admin"], "admin")
    portal.add_member(ta, w["lab"], "labeler")
    portal.add_member(tb, w["admin_b"], "admin")
    return w


def schema(w, sub, labels=None):
    return w["portal"].call("POST", "/schemas", sub, json={"labels": labels or [{"name": "car", "type": "rectangle"}]})


def create(w, sub, assets, key, **extra):
    sid = schema(w, sub).json()["id"]
    body = {"name": "wizard", "asset_ids": assets, "schema_version_id": sid, **extra}
    return w["portal"].call("POST", "/batches", sub, json=body, headers={"Idempotency-Key": key})


def test_media_listing_is_tenant_scoped_and_role_gated(world):
    w, p = world, world["portal"]
    got = {m["source_asset_id"] for m in p.call("GET", "/media", w["admin"]).json()["items"]}
    assert got == {w["ids"]["a1"], w["ids"]["a2"], w["ids"]["a3"]}  # tenant B's clip is not offered
    item = next(m for m in p.call("GET", "/media", w["admin"]).json()["items"] if m["source_asset_id"] == w["ids"]["a2"])
    assert item["frames"] > 0 and item["duration_s"] > 0 and "sha256" in item
    assert p.call("GET", "/media", w["lab"]).status_code == 403
    assert {m["source_asset_id"] for m in p.call("GET", "/media", w["admin_b"]).json()["items"]} == {w["ids"]["b1"]}


def test_schema_registration_dedupes_validates_and_is_tenant_scoped(world):
    w, p = world, world["portal"]
    first, second = schema(w, w["admin"]), schema(w, w["admin"])
    assert first.status_code == 201 and first.json()["id"] == second.json()["id"]
    assert schema(w, w["admin"], [{"name": "car", "type": "rectangle"}, {"name": "car", "type": "polygon"}]).status_code == 422
    assert schema(w, w["admin"], [{"name": "x", "type": "hologram"}]).status_code == 422
    assert [v["id"] for v in p.call("GET", "/schemas", w["admin"]).json()["items"]] == [first.json()["id"]]
    assert p.call("GET", "/schemas", w["admin_b"]).json()["items"] == []
    assert schema(w, w["lab"]).status_code == 403


def test_preview_matches_what_provisioning_creates_and_has_no_side_effects(world, session_factory):
    w, p = world, world["portal"]
    ids = [w["ids"]["a1"], w["ids"]["a2"], w["ids"]["a3"]]
    pv = p.call("POST", "/batches/preview", w["admin"], json={"asset_ids": ids, "segment_size": 40})
    assert pv.status_code == 200
    plan = pv.json()
    assert plan["totals"]["clips"] == 3 and plan["totals"]["tasks"] == 3 and plan["totals"]["jobs"] == sum(c["jobs"] for c in plan["clips"])
    with session_factory() as s:
        assert s.scalar(select(LabelingBatch).where(LabelingBatch.tenant_id == w["ta"])) is None  # preview wrote nothing
    r = create(w, w["admin"], ids, "k-prev", segment_size=40)
    assert r.status_code == 202, r.text
    p.run_operations(r.json()["operation_id"])
    jobs = p.call("GET", "/jobs", w["admin"], params={"limit": 200}).json()["items"]
    assert len(jobs) == plan["totals"]["jobs"]
    detail = p.call("GET", f"/batches/{r.json()['batch_id']}", w["admin"]).json()
    assert detail["provisioning"]["state"] == "complete" and detail["provisioning"]["jobs_created"] == len(jobs)
    assert detail["provisioning"]["tasks"] == {"total": 3, "ready": 3} and detail["provisioning"]["recovery"] == []


def test_preview_rejects_another_tenants_clip(world):
    w, p = world, world["portal"]
    r = p.call("POST", "/batches/preview", w["admin"], json={"asset_ids": [w["ids"]["b1"]]})
    assert r.status_code == 404
    assert create(w, w["admin"], [w["ids"]["b1"]], "k-x").status_code == 404
    assert p.call("POST", "/batches/preview", w["lab"], json={"asset_ids": [w["ids"]["a1"]]}).status_code == 403


def test_batch_settings_are_persisted_and_instructions_are_versioned(world):
    w, p = world, world["portal"]
    due = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    r1 = create(w, w["admin"], [w["ids"]["a1"]], "k-s1", priority=7, due_date=due.isoformat(), instructions="Box every car.", segment_size=30)
    r2 = create(w, w["admin"], [w["ids"]["a1"]], "k-s2", instructions="Box every car and truck.")
    d1 = p.call("GET", f"/batches/{r1.json()['batch_id']}", w["admin"]).json()["settings"]
    d2 = p.call("GET", f"/batches/{r2.json()['batch_id']}", w["admin"]).json()["settings"]
    assert d1["priority"] == 7 and d1["instructions"] == "Box every car." and d1["segment_size"] == 30
    assert datetime.fromisoformat(d1["due_date"]) == due
    assert d1["instructions_version"] and d2["instructions_version"] and d1["instructions_version"] != d2["instructions_version"]
    assert d1["review_policy"] == {"mode": "independent_all"}
    listing = {b["id"]: b for b in p.call("GET", "/batches", w["admin"]).json()["items"]}
    assert listing[r1.json()["batch_id"]]["priority"] == 7 and listing[r1.json()["batch_id"]]["clips"] == 1
    # same Idempotency-Key + same input is the same batch; settings do not create a second one
    again = create(w, w["admin"], [w["ids"]["a1"]], "k-s1", priority=7, due_date=due.isoformat(), instructions="Box every car.", segment_size=30)
    assert again.json()["batch_id"] == r1.json()["batch_id"]
    assert create(w, w["admin"], [w["ids"]["a1"], w["ids"]["a1"]], "k-dup").status_code == 422  # a clip only once


def test_partial_provisioning_is_visible_and_retry_recovers_without_duplicates(world, session_factory):
    w, p = world, world["portal"]
    ta = w["ta"]
    bad_id = f"bad-{ta}"
    bad_file = FIXTURES / f"p1_bad5_{ta}.mp4"
    bad_file.write_bytes(b"not a video")
    w["media"][(ta, bad_id)] = {**fx.make_media_ref(FIXTURES / "p1_c.mp4", bad_id, root=FIXTURES),
                                "storage": {"bucket": "fixtures", "key": bad_file.name, "version_id": None}}
    try:
        r = create(w, w["admin"], [w["ids"]["a1"], bad_id], "k-bad")
        batch_id, op_id = r.json()["batch_id"], r.json()["operation_id"]
        for _ in range(8):
            with session_factory() as s:
                s.get(Operation, uuid.UUID(op_id)).run_after = datetime.now(UTC)
                s.commit()
            p.run_operations(op_id)
        d = p.call("GET", f"/batches/{batch_id}", w["admin"]).json()["provisioning"]
        assert d["state"] == "failed" and d["tasks"]["ready"] == 1 and d["tasks"]["failed"] == 1 and d["error"]
        assert d["recovery"] == [{"action": "retry", "operation_id": op_id, "label": "Retry provisioning"}]
        assert p.call("POST", f"/operations/{op_id}/retry", w["lab"]).status_code == 403
        # repair the media, then use the recovery action
        shutil.copy(FIXTURES / "p1_c.mp4", bad_file)
        rr = p.call("POST", f"/operations/{op_id}/retry", w["admin"])
        assert rr.status_code == 202 and rr.json()["state"] == "queued"
        p.run_operations(op_id)
        d = p.call("GET", f"/batches/{batch_id}", w["admin"]).json()["provisioning"]
        assert d["state"] == "complete" and d["tasks"] == {"total": 2, "ready": 2} and d["recovery"] == []
        cvat_ids = [t.cvat_task_id for t in _tasks(session_factory, batch_id)]
        assert len(set(cvat_ids)) == 2  # one CVAT task per clip: no duplicates
        # a healthy operation cannot be "retried"
        assert p.call("POST", f"/operations/{op_id}/retry", w["admin"]).status_code == 409
    finally:
        bad_file.unlink(missing_ok=True)


def _tasks(sf, batch_id):
    from api.labeling.models import LabelingTask

    with sf() as s:
        return list(s.scalars(select(LabelingTask).where(LabelingTask.batch_id == uuid.UUID(batch_id))))


def test_metrics_overview_matches_records_and_labels_unavailable_data(world, session_factory):
    w, p = world, world["portal"]
    r = create(w, w["admin"], [w["ids"]["a1"], w["ids"]["a2"]], "k-m", segment_size=40)
    p.run_operations(r.json()["operation_id"])
    with session_factory() as s:
        jobs = list(s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == w["ta"]).order_by(LabelingJob.start_frame)))
        old = datetime.now(UTC) - timedelta(hours=5)
        jobs[0].portal_state, jobs[0].updated_at = "in_review", old
        for i, decision in enumerate(("approve", "approve", "reject", "submit")):
            s.add(ReviewEvent(tenant_id=w["ta"], job_id=jobs[i % len(jobs)].id, actor_id="rev", decision=decision, annotation_revision=1, details={}))
        s.commit()
        total_jobs = len(jobs)
    m = p.call("GET", "/metrics/overview", w["admin"]).json()
    assert sum(m["jobs_by_state"].values()) == total_jobs and m["jobs_by_state"]["in_review"] == 1
    assert m["throughput"]["approved_total"] == 2 and sum(m["throughput"]["approved_per_day"].values()) == 2
    assert m["rejection"] == {"rejected": 1, "approved": 2, "rate": 0.333}
    assert m["queue_age"]["in_review"]["jobs"] == 1 and m["queue_age"]["in_review"]["oldest_age_s"] >= 5 * 3600 - 5
    assert m["time_on_task"]["available"] is False and "not instrumented" in m["time_on_task"]["reason"]
    assert m["prelabel"]["runs"] == 0 and m["prelabel"]["inference_duration_s"] is None  # manual-only: nothing invented
    assert p.call("GET", "/metrics/overview", w["lab"]).status_code == 403
    assert p.call("GET", "/metrics/overview", w["admin_b"]).json()["jobs_by_state"] == {}  # tenant-scoped


def test_local_dir_media_catalog_cannot_cross_tenants(clips, tmp_path):
    (tmp_path / "t1").mkdir()
    (tmp_path / "t2").mkdir()
    shutil.copy(clips["p1_c"], tmp_path / "t1" / "one.mp4")
    shutil.copy(clips["p1_c"], tmp_path / "t2" / "two.mp4")
    cat = fx.LocalDirMediaCatalog(tmp_path)
    assert [m["source_asset_id"] for m in cat.list_assets("t1")] == ["one"]
    assert cat.get("t1", "one")["storage"]["key"] == "t1/one.mp4"
    for tenant, asset in (("t1", "two"), ("t2", "one"), ("t1", "../t2/two"), ("..", "one")):
        with pytest.raises(KeyError):
            cat.get(tenant, asset)
    assert cat.list_assets("nobody") == []
