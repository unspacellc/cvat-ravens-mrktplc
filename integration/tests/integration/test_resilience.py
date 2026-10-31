"""Phase 6 failure-mode qualification on real CVAT: worker crash, editor outage, concurrent review, secret rotation."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
import uvicorn
from sqlalchemy import select

from api.labeling import fixtures as fx
from api.labeling import operations, provisioning, service
from api.labeling.app import create_app
from api.labeling.cvat_client import CvatClient
from api.labeling.models import LabelingTask, Operation, TenantCvatOrg

from .conftest import FIXTURES, build_ctx
from .review_harness import LABELS
from .test_review_api import draw, w  # noqa: F401  (w is a fixture)
from .test_webhooks_reconcile import _inbox_count, _patch, _wait, provisioned, sign  # noqa: F401  (provisioned is a fixture)

pytestmark = pytest.mark.capability


def test_crashed_worker_lease_expires_and_another_worker_finishes_without_duplicates(session_factory, cvat_token, clips, tenant_ids):
    ta, _ = tenant_ids
    asset = f"a-{ta}"
    ctx = build_ctx(cvat_token, {(ta, asset): fx.make_media_ref(clips["p1_c"], asset, root=FIXTURES)})
    with session_factory() as s:
        schema = service.register_schema(s, ta, "1.0.0", LABELS)
        batch, op = service.create_batch(s, ctx, tenant_id=ta, name="crash", asset_ids=[asset], schema_version_id=schema.id,
                                         idempotency_key="crash", segment_size=40)
        s.commit()
        batch_id, op_id = batch.id, op.id
    with session_factory() as s:  # worker A claims the operation and dies without finishing: it stays "running"
        claimed = operations.claim_next(s, op_id=op_id, lease_s=300)
        s.commit()
        assert claimed.state == "running" and claimed.attempts == 1
    handlers = {"provision_batch": provisioning.provision_batch(ctx)}
    assert operations.run_one(session_factory, handlers, op_id=op_id) is False  # live lease: nobody else may take it
    with session_factory() as s:
        s.get(Operation, op_id).lease_until = operations.now() - timedelta(seconds=1)  # the lease runs out
        s.commit()
    while operations.run_one(session_factory, handlers, op_id=op_id):
        pass
    with session_factory() as s:
        op = s.get(Operation, op_id)
        tasks = list(s.scalars(select(LabelingTask).where(LabelingTask.batch_id == batch_id)))
        slug = s.get(TenantCvatOrg, (ta, ctx.settings.cvat_instance)).cvat_org_slug
    assert op.state == "succeeded" and op.attempts == 2
    assert [t.status for t in tasks] == ["ready"]
    names = [t["name"] for t in ctx.cvat._req("GET", "/api/tasks", org=slug).json()["results"]]
    assert names.count(f"lb-{tasks[0].id}") == 1


def test_editor_outage_gives_a_clean_retryable_error_changes_nothing_and_recovers(w):  # noqa: F811
    p = w["p"]
    j0, j1 = w["jobs"][0], w["jobs"][1]
    assert p.call("POST", f"/jobs/{j0['id']}/assignment", w["adm"], json={"user_id": w["lab"], "expected_revision": j0["revision"]}).status_code == 200
    draw(w, j0)
    healthy = p.ctx.cvat
    p.ctx.cvat = CvatClient("http://127.0.0.1:9", "unused", timeout=2)  # nothing listens there
    try:
        before = {j["id"]: (j["assignee_user_id"], j["revision"], j["portal_state"]) for j in p.call("GET", "/jobs", w["adm"]).json()["items"]}
        r = p.call("POST", f"/jobs/{j1['id']}/assignment", w["adm"], json={"user_id": w["lab"], "expected_revision": j1["revision"]})
        assert r.status_code == 503 and r.json()["code"] == "editor_unavailable" and r.json()["details"] == {"retryable": True}
        assert "127.0.0.1" not in r.text and r.json()["requestId"]  # standard envelope; upstream internals are not echoed
        r = p.call("POST", f"/jobs/{j0['id']}/transitions", w["adm"], json={"command": "submit", "expected_revision": j0["revision"] + 1, "reviewer_id": w["rev"]})
        assert r.status_code == 503
        after = {j["id"]: (j["assignee_user_id"], j["revision"], j["portal_state"]) for j in p.call("GET", "/jobs", w["adm"]).json()["items"]}
        assert after == before  # nothing half-applied; reads that need only the portal DB still work
    finally:
        p.ctx.cvat = healthy
    assert p.call("POST", f"/jobs/{j1['id']}/assignment", w["adm"], json={"user_id": w["lab"], "expected_revision": j1["revision"]}).status_code == 200
    cur = next(j for j in p.call("GET", "/jobs", w["adm"]).json()["items"] if j["id"] == j0["id"])
    r = p.call("POST", f"/jobs/{j0['id']}/transitions", w["lab"], json={"command": "submit", "expected_revision": cur["revision"], "reviewer_id": w["rev"]})
    assert r.status_code == 200 and r.json()["portal_state"] == "in_review"


def test_concurrent_approvals_produce_exactly_one_approval(w):  # noqa: F811
    p = w["p"]
    job = w["jobs"][0]
    assert p.call("POST", f"/jobs/{job['id']}/assignment", w["adm"], json={"user_id": w["lab"], "expected_revision": job["revision"]}).status_code == 200
    draw(w, job)
    cur = next(j for j in p.call("GET", "/jobs", w["adm"]).json()["items"] if j["id"] == job["id"])
    assert p.call("POST", f"/jobs/{job['id']}/transitions", w["lab"], json={"command": "submit", "expected_revision": cur["revision"], "reviewer_id": w["rev"]}).status_code == 200
    sha = p.call("GET", f"/jobs/{job['id']}/review", w["rev"]).json()["live_snapshot_sha256"]
    rev = next(j for j in p.call("GET", "/jobs", w["adm"]).json()["items"] if j["id"] == job["id"])["revision"]
    body = {"command": "approve", "expected_revision": rev, "attestation": "ok", "expected_snapshot_sha256": sha}
    gate = threading.Barrier(2)

    def approve(sub):
        gate.wait()
        return p.call("POST", f"/jobs/{job['id']}/transitions", sub, json=body).status_code

    with ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(approve, [w["rev"], w["adm"]]))
    assert codes[0] == 200 and 400 <= codes[1] < 500, codes  # one winner, the other refused as stale, never two approvals
    events = [e["decision"] for e in p.call("GET", f"/jobs/{job['id']}/review", w["adm"]).json()["events"]]
    assert events.count("approve") == 1
    assert next(j for j in p.call("GET", "/jobs", w["adm"]).json()["items"] if j["id"] == job["id"])["portal_state"] == "approved"


@contextmanager
def serve(settings, session_factory, port=8000):
    server = uvicorn.Server(uvicorn.Config(create_app(settings, session_factory), host="0.0.0.0", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    try:
        yield
    finally:
        server.should_exit = True
        t.join(timeout=10)


def test_webhook_secret_rotation_has_no_gap_and_retires_the_old_secret(session_factory, provisioned):  # noqa: F811
    ctx, tenant, org, jobs = provisioned
    cvat_jid = jobs[0][1]
    old_master = ctx.settings.webhook_master_secret
    states = iter(["in progress", "completed", "in progress", "completed", "in progress", "completed", "new"])

    def delivered() -> bool:
        before = _inbox_count(session_factory, resource_id=cvat_jid)
        _patch(ctx, org, cvat_jid, state=next(states))
        try:
            _wait(lambda: _inbox_count(session_factory, resource_id=cvat_jid) > before, timeout=8)
            return True
        except TimeoutError:
            return False

    with serve(ctx.settings, session_factory):
        assert delivered()  # baseline: CVAT signs with the current secret

    rotating = replace(ctx.settings, webhook_master_secret="rotated-master-secret", webhook_previous_master_secret=old_master)
    with serve(rotating, session_factory):
        assert delivered()  # master changed, CVAT not yet re-keyed: the previous secret keeps deliveries flowing

    retired_too_early = replace(ctx.settings, webhook_master_secret="rotated-master-secret", webhook_previous_master_secret="")
    with serve(retired_too_early, session_factory):
        assert not delivered()  # shows why re-keying is required: without it, CVAT's old signature is refused (and CVAT never retries a 4xx)
        with session_factory() as s:
            result = provisioning.rotate_webhook_secrets(s, replace(ctx, settings=retired_too_early), tenant_id=tenant)
        assert result == {"rotated": 1, "failed": []}
        assert delivered()  # after re-keying, CVAT signs with the new secret
        body = json.dumps({"event": "update:job", "webhook_id": org.cvat_webhook_id, "job": {"id": cvat_jid}}).encode()
        url = "http://localhost:8000/api/v1/labeling/webhooks/cvat"
        old_secret = ctx.settings.tenant_webhook_secret(tenant)
        assert httpx.post(url, content=body, headers={"x-signature-256": sign(old_secret, body)}).status_code == 401
        assert httpx.post(url, content=body, headers={"x-signature-256": sign(retired_too_early.tenant_webhook_secret(tenant), body)}).status_code == 202
