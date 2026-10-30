"""Phase 5 exit flow, over HTTP against real CVAT, with model services disabled (manual-only mode): an admin creates a multi-clip
batch through the wizard endpoints, a labeler annotates, a reviewer requests rework and later approves, and the admin publishes."""

import uuid
from dataclasses import replace

import pytest

from api.labeling import fixtures as fx

from .conftest import FIXTURES, build_ctx
from .portal import make_portal
from .test_review_api import draw, fresh, tr

pytestmark = pytest.mark.capability


def test_wizard_to_published_version_in_manual_only_mode(session_factory, cvat_token, clips, tenant_ids):
    ta, _ = tenant_ids
    u = uuid.uuid4().hex[:6]
    ids = {"short": f"short-{ta}", "long": f"long-{ta}"}
    media = {(ta, ids["short"]): fx.make_media_ref(clips["p1_c"], ids["short"], root=FIXTURES),
             (ta, ids["long"]): fx.make_media_ref(clips["p1_a"], ids["long"], root=FIXTURES)}
    ctx = build_ctx(cvat_token, media)  # no model catalog, no inference backend: manual-only
    ctx.settings = replace(ctx.settings, segment_size=40)
    p = make_portal(session_factory, ctx)
    w = {"p": p, "ta": ta, "adm": f"adm-{u}", "lab": f"lab-{u}", "rev": f"rev-{u}"}
    for sub, role in ((w["adm"], "admin"), (w["lab"], "labeler"), (w["rev"], "reviewer")):
        p.add_member(ta, sub, role)

    # --- wizard: choose media, define labels, preview the split, create with settings
    offered = {m["source_asset_id"] for m in p.call("GET", "/media", w["adm"]).json()["items"]}
    assert offered == set(ids.values())
    schema_id = p.call("POST", "/schemas", w["adm"], json={"labels": [{"name": "car", "type": "rectangle"}]}).json()["id"]
    plan = p.call("POST", "/batches/preview", w["adm"], json={"asset_ids": list(ids.values()), "segment_size": 40}).json()
    assert plan["totals"]["clips"] == 2 and plan["totals"]["jobs"] >= 2
    r = p.call("POST", "/batches", w["adm"], headers={"Idempotency-Key": "flow-1"}, json={
        "name": "Multi-clip POC", "asset_ids": list(ids.values()), "schema_version_id": schema_id, "segment_size": 40,
        "instructions": "Box every car.", "priority": 5})
    assert r.status_code == 202, r.text
    batch_id = r.json()["batch_id"]
    p.run_operations(r.json()["operation_id"])
    detail = p.call("GET", f"/batches/{batch_id}", w["adm"]).json()
    assert detail["provisioning"]["state"] == "complete" and detail["provisioning"]["tasks"] == {"total": 2, "ready": 2}
    jobs = p.call("GET", "/jobs", w["adm"], params={"limit": 200}).json()["items"]
    assert len(jobs) == plan["totals"]["jobs"] == detail["provisioning"]["jobs_created"]

    # --- manual-only mode: no models are offered, starting a run is refused cleanly, labeling is unaffected
    assert p.call("GET", "/models", w["adm"]).json() == {"items": []}
    refused = p.call("POST", f"/batches/{batch_id}/prelabel-runs", w["adm"], headers={"Idempotency-Key": "pl"}, json={"model_id": "any"})
    assert 400 <= refused.status_code < 500 and refused.json()["code"]

    # --- labeler annotates every job (a real CVAT write each) and submits
    for j in jobs:
        assert p.call("POST", f"/jobs/{j['id']}/assignment", w["adm"], json={"user_id": w["lab"], "expected_revision": j["revision"]}).status_code == 200
        draw(w, j)
        assert tr(w, j["id"], w["lab"], "submit", reviewer_id=w["rev"]).json()["portal_state"] == "in_review"

    # --- reviewer requests rework on one job; the labeler fixes and resubmits
    first = jobs[0]["id"]
    assert tr(w, first, w["rev"], "reject", reason="loose box", issues=[{"frame": 0, "text": "tighten"}]).json()["portal_state"] == "rework"
    issue_id = p.call("GET", f"/jobs/{first}/review", w["lab"]).json()["issues"][0]["id"]
    draw(w, jobs[0])
    assert tr(w, first, w["lab"], "submit", reviewer_id=w["rev"], resolved_issue_ids=[issue_id]).status_code == 200

    # --- reviewer approves everything against the content they were shown
    for j in jobs:
        sha = p.call("GET", f"/jobs/{j['id']}/review", w["rev"]).json()["live_snapshot_sha256"]
        assert tr(w, j["id"], w["rev"], "approve", attestation="reviewed all frames", expected_snapshot_sha256=sha).json()["portal_state"] == "approved"

    # --- operational metrics reflect what just happened
    m = p.call("GET", "/metrics/overview", w["adm"]).json()
    assert m["jobs_by_state"] == {"approved": len(jobs)}
    assert m["throughput"]["approved_total"] == len(jobs)
    assert m["rejection"]["rejected"] == 1 and m["rejection"]["rate"] == round(1 / (len(jobs) + 1), 3)
    assert m["prelabel"]["runs"] == 0 and m["time_on_task"]["available"] is False

    # --- admin publishes one immutable version
    so = p.call("POST", f"/batches/{batch_id}/send-offs", w["adm"], headers={"Idempotency-Key": "flow-so"})
    assert so.status_code == 202, so.text
    p.run_operations(so.json()["operation_id"])
    dv = p.call("GET", f"/dataset-versions/{so.json()['dataset_version_id']}", w["adm"]).json()
    assert dv["status"] == "published" and dv["manifest_sha256"]
    assert fresh(w, first, w["adm"])["portal_state"] == "sent_off"  # delivered work leaves the review states
    row = next(b for b in p.call("GET", "/batches", w["adm"]).json()["items"] if b["id"] == batch_id)
    assert row["clips"] == 2 and row["job_counts"] == {"sent_off": len(jobs)} and row["priority"] == 5
