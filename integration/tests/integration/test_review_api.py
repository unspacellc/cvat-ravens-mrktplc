"""Review + send-off through the portal API: roles, tenant isolation, error codes, and the whole journey over HTTP."""

import uuid
from dataclasses import replace

import pytest

from api.labeling import fixtures as fx
from api.labeling.models import LabelingJob, LabelingTask, TenantCvatOrg

from .conftest import FIXTURES, build_ctx
from .portal import make_batch, make_portal

pytestmark = pytest.mark.capability


@pytest.fixture
def w(session_factory, cvat_token, clips, tenant_ids):
    ta, tb = tenant_ids
    u = uuid.uuid4().hex[:6]
    asset_a, asset_b = f"a-{ta}", f"b-{tb}"
    media = {(ta, asset_a): fx.make_media_ref(clips["p1_c"], asset_a, root=FIXTURES),
             (tb, asset_b): fx.make_media_ref(clips["p1_c"], asset_b, root=FIXTURES)}
    ctx = build_ctx(cvat_token, media)
    ctx.settings = replace(ctx.settings, segment_size=40)
    portal = make_portal(session_factory, ctx)
    out = {"p": portal, "ta": ta, "tb": tb, "adm": f"adm-{u}", "lab": f"lab-{u}", "rev": f"rev-{u}", "rev2": f"rev2-{u}", "adm_b": f"admb-{u}"}
    for sub, role in ((out["adm"], "admin"), (out["lab"], "labeler"), (out["rev"], "reviewer"), (out["rev2"], "reviewer")):
        portal.add_member(ta, sub, role)
    portal.add_member(tb, out["adm_b"], "admin")
    make_batch(portal, ta, out["adm"], asset_a)
    out["jobs"] = portal.call("GET", "/jobs", out["adm"]).json()["items"]
    out["batch_id"] = out["jobs"][0]["batch_id"]
    return out


def draw(w, job):
    p = w["p"]
    with p.session_factory() as s:
        j = s.get(LabelingJob, uuid.UUID(job["id"]))
        t = s.get(LabelingTask, j.task_id)
        slug = s.get(TenantCvatOrg, (w["ta"], "primary")).cvat_org_slug
        lid = next(iter(p.ctx.cvat.task_labels(slug, t.cvat_task_id)))
        p.ctx.cvat.put_job_annotations(slug, j.cvat_job_id, {"version": 0, "tags": [], "tracks": [], "shapes": [
            {"type": "rectangle", "frame": j.start_frame, "label_id": lid, "points": [5, 5, 60, 60], "occluded": False, "attributes": [],
             "source": "manual", "z_order": 0, "rotation": 0, "outside": False}]})


def fresh(w, job_id, sub):
    return next(j for j in w["p"].call("GET", "/jobs", sub).json()["items"] if j["id"] == job_id)


def tr(w, job_id, sub, command, **body):
    j = fresh(w, job_id, w["adm"])
    return w["p"].call("POST", f"/jobs/{job_id}/transitions", sub, json={"command": command, "expected_revision": j["revision"], **body})


def test_full_journey_over_http(w):
    p = w["p"]
    for j in w["jobs"]:
        assert p.call("POST", f"/jobs/{j['id']}/assignment", w["adm"], json={"user_id": w["lab"], "expected_revision": j["revision"]}).status_code == 200
        draw(w, j)
    first = w["jobs"][0]["id"]

    # labeler submits; error shapes are the shared envelope
    r = tr(w, first, w["lab"], "submit", reviewer_id=w["lab"])
    assert r.status_code == 422 and r.json()["code"] == "invalid_command"
    stale = p.call("POST", f"/jobs/{first}/transitions", w["lab"], json={"command": "submit", "expected_revision": 999, "reviewer_id": w["rev"]})
    assert stale.status_code == 409 and stale.json()["code"] == "revision_conflict"
    assert tr(w, first, w["lab"], "submit", reviewer_id=w["rev"]).json()["portal_state"] == "in_review"

    # tenant B and the wrong people get 404/403, not data
    assert p.call("POST", f"/jobs/{first}/transitions", w["adm_b"], tenant=w["tb"], json={"command": "approve", "expected_revision": 1}).status_code == 404
    assert tr(w, first, w["rev2"], "approve", attestation="x", expected_snapshot_sha256="0" * 64).status_code == 403  # not the assigned reviewer
    assert p.call("GET", f"/jobs/{first}/review", w["lab"]).status_code == 404  # labeler no longer sees it (reviewer is assignee)

    rv = p.call("GET", f"/jobs/{first}/review", w["rev"]).json()
    sha = rv["live_snapshot_sha256"]
    assert sha and rv["portal_state"] == "in_review"
    rej = tr(w, first, w["rev"], "reject", reason="loose box", issues=[{"frame": 0, "text": "tighten", "position": [5, 5, 60, 60]}])
    assert rej.json()["portal_state"] == "rework"
    review = p.call("GET", f"/jobs/{first}/review", w["lab"]).json()
    issue_id = review["issues"][0]["id"]
    assert review["issues"][0]["status"] == "open" and [e["decision"] for e in review["events"]] == ["submit", "reject"]

    assert tr(w, first, w["lab"], "submit", reviewer_id=w["rev"]).status_code == 422  # unresolved blocking issue
    draw(w, w["jobs"][0])
    assert tr(w, first, w["lab"], "submit", reviewer_id=w["rev"], resolved_issue_ids=[issue_id]).status_code == 200
    sha = p.call("GET", f"/jobs/{first}/review", w["rev"]).json()["live_snapshot_sha256"]
    assert tr(w, first, w["rev"], "approve", attestation="all frames reviewed", expected_snapshot_sha256=sha).json()["portal_state"] == "approved"

    # send-off is refused until every job is approved, with the reasons listed
    bad = p.call("POST", f"/batches/{w['batch_id']}/send-offs", w["adm"], headers={"Idempotency-Key": "so"})
    assert bad.status_code == 409 and bad.json()["code"] == "not_eligible" and bad.json()["details"]["reasons"]
    assert p.call("POST", f"/batches/{w['batch_id']}/send-offs", w["rev"], headers={"Idempotency-Key": "so"}).status_code == 403

    second = w["jobs"][1]["id"]
    assert tr(w, second, w["lab"], "submit", reviewer_id=w["rev"]).status_code == 200
    sha2 = p.call("GET", f"/jobs/{second}/review", w["rev"]).json()["live_snapshot_sha256"]
    assert tr(w, second, w["rev"], "approve", attestation="ok", expected_snapshot_sha256=sha2).status_code == 200

    ok = p.call("POST", f"/batches/{w['batch_id']}/send-offs", w["adm"], headers={"Idempotency-Key": "so"})
    assert ok.status_code == 202
    dvid, opid = ok.json()["dataset_version_id"], ok.json()["operation_id"]
    p.run_operations(opid)
    assert p.call("GET", f"/operations/{opid}", w["adm"]).json()["state"] == "succeeded"
    dv = p.call("GET", f"/dataset-versions/{dvid}", w["adm"]).json()
    assert dv["status"] == "published" and dv["manifest_sha256"]
    assert p.call("GET", f"/dataset-versions/{dvid}", w["adm_b"], tenant=w["tb"]).status_code == 404
    assert p.call("GET", f"/batches/{w['batch_id']}", w["adm"]).json()["dataset_version"] == {"id": dvid, "status": "published"}
    # frozen: further transitions are conflicts, the same key replays to the same version
    assert tr(w, first, w["adm"], "reopen", reason="again").status_code == 409
    replay = p.call("POST", f"/batches/{w['batch_id']}/send-offs", w["adm"], headers={"Idempotency-Key": "so"})
    assert replay.json() == {"dataset_version_id": dvid, "operation_id": opid}

    # revision batch via API
    rb = p.call("POST", f"/dataset-versions/{dvid}/revision-batches", w["adm"], headers={"Idempotency-Key": "rb"})
    assert rb.status_code == 202
    p.run_operations(rb.json()["operation_id"])
    detail = p.call("GET", f"/batches/{rb.json()['batch_id']}", w["adm"]).json()
    assert detail["lifecycle"] == "created" and detail["predecessor_version_id"] == dvid


def test_reviewers_listing_is_tenant_scoped_and_minimal(w):
    p = w["p"]
    items = p.call("GET", "/reviewers", w["lab"]).json()["items"]
    assert {i["user_id"] for i in items} == {w["rev"], w["rev2"]} and all(set(i) == {"user_id", "email"} for i in items)
    assert p.call("GET", "/reviewers", w["adm_b"], tenant=w["tb"]).json()["items"] == []
