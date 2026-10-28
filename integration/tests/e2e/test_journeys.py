"""Phase 2 exit journeys against real CVAT, with per-user sessions created by each person's own IdP login.
No shared admin session is used on the user side: the only privileged credential is the portal's service token."""

import uuid
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from api.labeling import fixtures as fx
from api.labeling.app import create_app
from api.labeling.auth import JwksKeys, JwtConfig, JwtIdentity
from api.labeling.config import org_slug_for

from ..integration.conftest import FIXTURES, build_ctx
from ..integration.portal import Portal, make_batch
from .conftest import CVAT, ISSUER, PORTAL_CLIENT, cvat_session, idp_access_token

pytestmark = pytest.mark.capability


@pytest.fixture
def world(session_factory, cvat_token, clips, tenant_ids):
    ta, tb = tenant_ids
    u = uuid.uuid4().hex[:6]
    a_asset, b_asset = f"a-{ta}", f"b-{tb}"
    media = {(ta, a_asset): fx.make_media_ref(clips["p1_c"], a_asset, root=FIXTURES),
             (tb, b_asset): fx.make_media_ref(clips["p1_c"], b_asset, root=FIXTURES)}
    ctx = build_ctx(cvat_token, media)
    ctx.settings = replace(ctx.settings, segment_size=40)
    identity = JwtIdentity(JwtConfig(ISSUER, (PORTAL_CLIENT,)), JwksKeys(f"{ISSUER}/jwks"), session_factory)
    client = TestClient(create_app(ctx.settings, session_factory, identity=identity, ctx=ctx))
    portal = Portal(client, None, ctx, session_factory)
    names = {k: f"{k}-{u}" for k in ("admin", "lab1", "lab2", "rev", "admin_b", "lab_b")}
    portal.add_member(ta, names["admin"], "admin")
    for k, role in (("lab1", "labeler"), ("lab2", "labeler"), ("rev", "reviewer")):
        portal.add_member(ta, names[k], role)
    portal.add_member(tb, names["admin_b"], "admin")
    portal.add_member(tb, names["lab_b"], "labeler")

    def call(method, path, who, **kw):  # real IdP-issued bearer token for `who`
        kw.setdefault("headers", {})["Authorization"] = f"Bearer {idp_access_token(names[who])}"
        return client.request(method, f"/api/v1/labeling{path}", **kw)

    portal.token = lambda sub, **_: idp_access_token(sub)  # make_batch goes through the same real-token path
    batch_a = make_batch(portal, ta, names["admin"], a_asset)
    batch_b = make_batch(portal, tb, names["admin_b"], b_asset)
    jobs_a = call("GET", "/jobs", "admin").json()["items"]
    jobs_b = call("GET", "/jobs", "admin_b").json()["items"]
    return dict(call=call, names=names, ta=ta, tb=tb, jobs_a=jobs_a, jobs_b=jobs_b, portal=portal, batch_a=batch_a, batch_b=batch_b)


def cvat_job_id(w, job):
    from sqlalchemy import select

    from api.labeling.models import LabelingJob
    with w["portal"].session_factory() as s:
        return s.scalar(select(LabelingJob.cvat_job_id).where(LabelingJob.id == job["id"]))


def assign(w, job, who, rev=None):
    return w["call"]("POST", f"/jobs/{job['id']}/assignment", "admin",
                     json={"user_id": w["names"][who], "expected_revision": job["revision"] if rev is None else rev})


def launch(w, job, who):
    r = w["call"]("POST", f"/jobs/{job['id']}/editor-launch", who)
    assert r.status_code == 200, r.text
    return cvat_session(r.json()["url"], w["names"][who])


def test_labeler_journey_own_job_only(world):
    w = world
    j1, j2 = w["jobs_a"]
    assert assign(w, j1, "lab1").status_code == 200
    s = launch(w, j1, "lab1")
    assert s.last.status_code in (200, 302) and s.get("/api/users/self").json()["username"].startswith("lb-u-")
    assert s.get(f"/api/jobs/{cvat_job_id(w, j1)}").status_code == 200
    assert s.get(f"/api/jobs/{cvat_job_id(w, j1)}/annotations").status_code == 200
    # unassigned job in the same org + other tenant's job: denied by CVAT itself, not by hidden navigation
    assert s.get(f"/api/jobs/{cvat_job_id(w, j2)}").status_code in (403, 404)
    assert s.get(f"/api/jobs/{cvat_job_id(w, j2)}/data/meta").status_code in (403, 404)
    assert s.get(f"/api/jobs/{cvat_job_id(w, w['jobs_b'][0])}").status_code in (403, 404)
    assert s.get(f"/api/jobs/{cvat_job_id(w, j1)}", headers={"X-Organization": "someone-elses-org"}).status_code in (403, 404)


def test_reassignment_takes_effect_in_cvat_immediately(world):
    w = world
    j1 = w["jobs_a"][0]
    r1 = assign(w, j1, "lab1")
    s1 = launch(w, j1, "lab1")
    cid = cvat_job_id(w, j1)
    assert s1.get(f"/api/jobs/{cid}").status_code == 200
    assert assign(w, j1, "lab2", rev=r1.json()["revision"]).status_code == 200
    assert s1.get(f"/api/jobs/{cid}").status_code in (403, 404)  # existing session, no re-login needed
    assert w["call"]("POST", f"/jobs/{j1['id']}/editor-launch", "lab1").status_code == 404
    assert launch(w, j1, "lab2").get(f"/api/jobs/{cid}").status_code == 200


def test_removed_member_loses_cvat_access_and_cannot_log_back_in(world):
    w = world
    j1 = w["jobs_a"][0]
    assign(w, j1, "lab1")
    s = launch(w, j1, "lab1")
    cid = cvat_job_id(w, j1)
    assert s.get(f"/api/jobs/{cid}").status_code == 200
    assert w["call"]("DELETE", f"/members/{w['names']['lab1']}", "admin").status_code == 204
    assert s.get("/api/users/self").status_code == 401  # live session ended
    again = cvat_session(f"{CVAT}/api/auth/social/oidc/portal/login/?process=login&next=/", w["names"]["lab1"])
    assert again.get("/api/users/self").status_code == 401  # deactivated account cannot sign back in


def test_reviewer_opens_only_assigned_work_and_admin_sees_only_own_tenant(world):
    w = world
    j1, j2 = w["jobs_a"]
    assign(w, j1, "lab1")
    assert assign(w, j2, "rev").status_code == 200  # reviewer assigned for review
    org_a, org_b = {"X-Organization": org_slug_for(w["ta"])}, {"X-Organization": org_slug_for(w["tb"])}
    assert w["call"]("POST", f"/jobs/{j1['id']}/editor-launch", "rev").status_code == 403  # portal: not theirs
    rev = launch(w, j2, "rev")
    assert rev.get(f"/api/jobs/{cvat_job_id(w, j2)}", headers=org_a).status_code == 200
    assert rev.get(f"/api/jobs/{cvat_job_id(w, j1)}", headers=org_a).status_code in (403, 404)  # CVAT: same
    assert rev.get(f"/api/jobs/{cvat_job_id(w, w['jobs_b'][0])}", headers=org_b).status_code in (403, 404)
    adm = launch(w, j1, "admin")  # tenant admin (CVAT maintainer) sees the whole org, nothing outside it
    assert adm.get(f"/api/jobs/{cvat_job_id(w, j2)}", headers=org_a).status_code == 200
    assert adm.get(f"/api/jobs/{cvat_job_id(w, w['jobs_b'][0])}", headers=org_a).status_code in (403, 404)
    assert adm.get(f"/api/jobs/{cvat_job_id(w, w['jobs_b'][0])}", headers=org_b).status_code in (403, 404)
    assert launch(w, w["jobs_b"][0], "admin_b").get(f"/api/jobs/{cvat_job_id(w, j1)}", headers=org_a).status_code in (403, 404)


def test_portal_rejects_tokens_not_issued_for_it(world):
    w = world
    other = idp_access_token(w["names"]["lab1"], client_id="some-other-app")
    r = w["portal"].client.get("/api/v1/labeling/jobs", headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 401
    assert httpx.get(f"{CVAT}/api/jobs/{cvat_job_id(w, w['jobs_a'][0])}").status_code == 401
