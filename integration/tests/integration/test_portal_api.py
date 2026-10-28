"""Portal API against real CVAT: server-enforced tenant + assignment scoping, role checks, optimistic concurrency, launch."""

import uuid
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from api.labeling import fixtures as fx

from .conftest import FIXTURES, build_ctx
from .portal import make_batch, make_portal

pytestmark = pytest.mark.capability


@pytest.fixture
def world(session_factory, cvat_token, clips, tenant_ids):
    ta, tb = tenant_ids
    u = uuid.uuid4().hex[:6]
    asset_a, asset_b = f"a-{ta}", f"b-{tb}"
    media = {(ta, asset_a): fx.make_media_ref(clips["p1_c"], asset_a, root=FIXTURES),
             (tb, asset_b): fx.make_media_ref(clips["p1_c"], asset_b, root=FIXTURES)}
    ctx = build_ctx(cvat_token, media)
    ctx.settings = replace(ctx.settings, segment_size=40)  # 60-frame clip -> 2 jobs
    portal = make_portal(session_factory, ctx)
    w = {"portal": portal, "ta": ta, "tb": tb, "admin_a": f"admin-a-{u}", "lab1": f"lab1-{u}", "lab2": f"lab2-{u}",
         "rev": f"rev-{u}", "admin_b": f"admin-b-{u}", "lab_b": f"labb-{u}"}
    portal.add_member(ta, w["admin_a"], "admin")
    portal.add_member(ta, w["lab1"], "labeler")
    portal.add_member(ta, w["lab2"], "labeler")
    portal.add_member(ta, w["rev"], "reviewer")
    portal.add_member(tb, w["admin_b"], "admin")
    portal.add_member(tb, w["lab_b"], "labeler")
    w["batch_a"] = make_batch(portal, ta, w["admin_a"], asset_a)
    w["batch_b"] = make_batch(portal, tb, w["admin_b"], asset_b)
    w["jobs_a"] = portal.call("GET", "/jobs", w["admin_a"]).json()["items"]
    w["jobs_b"] = portal.call("GET", "/jobs", w["admin_b"]).json()["items"]
    return w


def assign(w, job, user, sub=None, rev=None):
    return w["portal"].call("POST", f"/jobs/{job['id']}/assignment", sub or w["admin_a"],
                            json={"user_id": user, "expected_revision": job["revision"] if rev is None else rev})


def test_listing_is_tenant_scoped_and_labelers_only_see_assigned_jobs(world):
    w, p = world, world["portal"]
    assert len(w["jobs_a"]) == 2 and len(w["jobs_b"]) == 2
    assert {j["batch_id"] for j in w["jobs_a"]}.isdisjoint({j["batch_id"] for j in w["jobs_b"]})
    assert p.call("GET", "/jobs", w["lab1"]).json()["items"] == []
    assert assign(w, w["jobs_a"][0], w["lab1"]).status_code == 200
    mine = p.call("GET", "/jobs", w["lab1"]).json()["items"]
    assert [j["id"] for j in mine] == [w["jobs_a"][0]["id"]]
    assert p.call("GET", "/jobs", w["lab2"]).json()["items"] == []
    # filters cannot widen a labeler's view
    assert p.call("GET", "/jobs", w["lab2"], params={"assignee": w["lab1"]}).json()["items"] == []
    assert len(p.call("GET", "/jobs", w["rev"]).json()["items"]) == 2
    assert all(j["sync_age_s"] is not None for j in p.call("GET", "/jobs", w["admin_a"]).json()["items"])


def test_direct_access_across_assignee_and_tenant_is_not_found(world):
    w, p = world, world["portal"]
    job = w["jobs_a"][0]
    assert assign(w, job, w["lab1"]).status_code == 200
    assert p.call("POST", f"/jobs/{job['id']}/editor-launch", w["lab2"]).status_code == 404  # same tenant, not assigned
    assert p.call("POST", f"/jobs/{job['id']}/editor-launch", w["lab_b"]).status_code == 404  # other tenant
    assert p.call("POST", f"/jobs/{job['id']}/editor-launch", w["admin_b"]).status_code == 404
    assert assign(w, job, w["lab_b"], sub=w["admin_b"]).status_code == 404  # B admin cannot touch A's job
    assert p.call("GET", f"/batches/{w['batch_a']['batch_id']}", w["admin_b"]).status_code == 404
    assert p.call("GET", f"/operations/{w['batch_a']['operation_id']}", w["admin_b"]).status_code == 404


def test_assignee_must_be_an_active_labeler_in_the_callers_tenant(world):
    w = world
    job = w["jobs_a"][0]
    for bad in (w["lab_b"], w["admin_a"], "nobody"):  # other tenant, admin without labeler role, unknown
        r = assign(w, job, bad)
        assert r.status_code == 422 and r.json()["code"] == "invalid_assignee", (bad, r.text)


def test_roles_and_authentication_are_enforced(world):
    w, p = world, world["portal"]
    job = w["jobs_a"][0]
    assert assign(w, job, w["lab1"], sub=w["lab1"]).status_code == 403
    assert assign(w, job, w["lab1"], sub=w["rev"]).status_code == 403
    assert p.call("GET", "/batches", w["lab1"]).status_code == 403
    assert p.call("PUT", f"/members/{w['lab1']}", w["rev"], json={"roles": ["admin"]}).status_code == 403
    assert p.call("GET", "/jobs", None).status_code == 401
    r = p.call("GET", "/jobs", "stranger")
    assert r.status_code == 403 and r.json()["code"] == "not_a_member"
    bad = p.client.get("/api/v1/labeling/jobs", headers={"Authorization": f"Bearer {p.token(w['lab1'], exp=-5)}"})
    assert bad.status_code == 401 and bad.json()["code"] == "invalid_token"
    assert {"error", "statusCode", "code", "details", "requestId"} <= bad.json().keys()


def test_assignment_uses_optimistic_revision(world):
    w = world
    job = w["jobs_a"][0]
    assert assign(w, job, w["lab1"]).status_code == 200
    stale = assign(w, job, w["lab2"])  # still carries the pre-assignment revision
    assert stale.status_code == 409 and stale.json()["code"] == "revision_conflict"


def test_reassignment_moves_access_in_portal_and_in_cvat(world, cvat_token):
    w, p = world, world["portal"]
    job = w["jobs_a"][0]
    r1 = assign(w, job, w["lab1"])
    assert p.call("POST", f"/jobs/{job['id']}/editor-launch", w["lab1"]).status_code == 200
    r2 = assign(w, job, w["lab2"], rev=r1.json()["revision"])
    assert r2.status_code == 200
    assert p.call("POST", f"/jobs/{job['id']}/editor-launch", w["lab1"]).status_code == 404  # portal: no longer assigned
    ok = p.call("POST", f"/jobs/{job['id']}/editor-launch", w["lab2"])
    assert ok.status_code == 200
    q = parse_qs(urlsplit(ok.json()["url"]).query)
    assert q["process"] == ["login"] and q["next"][0].startswith("/tasks/") and "/jobs/" in q["next"][0]
    assert urlsplit(ok.json()["url"]).netloc == urlsplit(p.ctx.settings.cvat_public_url).netloc


def test_tenant_selection_for_multi_tenant_users(world):
    w, p = world, world["portal"]
    shared = f"multi-{uuid.uuid4().hex[:6]}"
    p.add_member(w["ta"], shared, "reviewer")
    p.add_member(w["tb"], shared, "labeler")
    amb = p.call("GET", "/jobs", shared)
    assert amb.status_code == 409 and amb.json()["code"] == "tenant_selection_required"
    assert len(p.call("GET", "/jobs", shared, tenant=w["ta"]).json()["items"]) == 2
    assert p.call("GET", "/jobs", shared, tenant=w["tb"]).json()["items"] == []  # labeler in B, nothing assigned
    assert p.call("GET", "/jobs", shared, tenant="tenant-i-am-not-in").status_code == 403


def test_removing_a_member_unassigns_their_jobs(world):
    w, p = world, world["portal"]
    job = w["jobs_a"][0]
    assert assign(w, job, w["lab1"]).status_code == 200
    assert p.call("DELETE", f"/members/{w['lab1']}", w["admin_a"]).status_code == 204
    assert p.call("GET", "/jobs", w["lab1"]).status_code == 403  # membership revoked
    after = {j["id"]: j for j in p.call("GET", "/jobs", w["admin_a"]).json()["items"]}
    assert after[job["id"]]["assignee_user_id"] is None
    assert p.call("DELETE", f"/members/{w['admin_a']}", w["admin_a"]).status_code == 422


def test_batch_detail_reports_counts_and_operation(world):
    w, p = world, world["portal"]
    d = p.call("GET", f"/batches/{w['batch_a']['batch_id']}", w["rev"]).json()
    assert sum(d["job_counts"].values()) == 2 and d["operation"]["state"] == "succeeded"
