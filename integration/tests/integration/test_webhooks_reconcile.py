import hashlib
import hmac
import json
import threading
import time

import httpx
import pytest
import uvicorn
from sqlalchemy import func, select

from api.labeling import fixtures as fx
from api.labeling import operations, provisioning, reconcile, service, webhooks
from api.labeling.app import create_app
from api.labeling.models import LabelingJob, TenantCvatOrg, WebhookInbox

from .conftest import FIXTURES, build_ctx

pytestmark = pytest.mark.capability
LABELS = [{"name": "car", "type": "rectangle", "attributes": []}]
SECRET = "unit-secret"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---- unit-level ---------------------------------------------------------------------------------------------
def test_signature_verification():
    body = b'{"a":1}'
    assert webhooks.verify_signature(SECRET, body, sign(SECRET, body))
    assert not webhooks.verify_signature(SECRET, body, sign("other", body))
    assert not webhooks.verify_signature(SECRET, body + b" ", sign(SECRET, body))  # raw bytes matter
    assert not webhooks.verify_signature(SECRET, body, None)
    assert not webhooks.verify_signature(SECRET, body, "sha1=" + "0" * 40)


def test_dedupe_key_collapses_redelivery_but_not_successive_updates():
    def payload(stamp):
        return {"event": "update:job", "webhook_id": 1, "job": {"id": 7, "updated_date": stamp}}

    k = lambda p: webhooks.derive_key(p, json.dumps(p).encode())[0]  # noqa: E731
    assert k(payload("2026-10-28T10:00:00Z")) == k(payload("2026-10-28T10:00:00Z"))
    assert k(payload("2026-10-28T10:00:00Z")) != k(payload("2026-10-28T10:00:05Z"))
    d = {"event": "delete:job", "webhook_id": 1, "job": {"id": 7, "updated_date": "x"}}
    assert webhooks.derive_key(d, b"1")[0] != webhooks.derive_key(d, b"2")[0]  # deletes fall back to body hash


# ---- end-to-end with real CVAT deliveries --------------------------------------------------------------------
@pytest.fixture
def provisioned(session_factory, cvat_token, clips, tenant_ids):
    ta, _ = tenant_ids
    media = {(ta, f"p1_c-{ta}"): fx.make_media_ref(clips["p1_c"], f"p1_c-{ta}", root=FIXTURES)}
    ctx = build_ctx(cvat_token, media)
    with session_factory() as s:
        schema = service.register_schema(s, ta, "1.0.0", LABELS)
        batch, op = service.create_batch(s, ctx, tenant_id=ta, name="wh", asset_ids=[f"p1_c-{ta}"], schema_version_id=schema.id,
                                         idempotency_key="wh-key", segment_size=40)
        s.commit()
        op_id = op.id
    handlers = {"provision_batch": provisioning.provision_batch(ctx)}
    while operations.run_one(session_factory, handlers, op_id=op_id):
        pass
    with session_factory() as s:
        jobs = s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == ta).order_by(LabelingJob.start_frame)).all()
        org = s.get(TenantCvatOrg, (ta, ctx.settings.cvat_instance))
        assert len(jobs) == 2
        return ctx, ta, org, [(j.id, j.cvat_job_id) for j in jobs]


@pytest.fixture
def live_app(session_factory, provisioned):
    ctx = provisioned[0]
    server = uvicorn.Server(uvicorn.Config(create_app(ctx.settings, session_factory), host="0.0.0.0", port=8000, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield server
    server.should_exit = True
    t.join(timeout=10)


def _wait(pred, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if (v := pred()):
            return v
        time.sleep(0.5)
    raise TimeoutError


def _inbox_count(session_factory, event="update:job", resource_id=None):
    with session_factory() as s:
        q = select(func.count()).select_from(WebhookInbox).where(WebhookInbox.event == event)
        if resource_id is not None:
            q = q.where(WebhookInbox.resource_id == resource_id)
        return s.scalar(q)


def _patch(ctx, org, cvat_job_id, **body):
    return ctx.cvat._req("PATCH", f"/api/jobs/{cvat_job_id}", org=org.cvat_org_slug, json=body)


def _job(session_factory, job_id):
    with session_factory() as s:
        return s.get(LabelingJob, job_id)


def test_bad_or_unknown_deliveries_are_rejected_with_401(session_factory, provisioned, live_app):
    ctx, _, org, _ = provisioned
    body = json.dumps({"event": "update:job", "webhook_id": org.cvat_webhook_id, "job": {"id": 1, "updated_date": "x"}}).encode()
    url = "http://localhost:8000/api/v1/labeling/webhooks/cvat"
    assert httpx.post(url, content=body).status_code == 401                                           # unsigned
    assert httpx.post(url, content=body, headers={"x-signature-256": sign("forged", body)}).status_code == 401
    unknown = json.dumps({"event": "update:job", "webhook_id": 999999999, "job": {"id": 1}}).encode()
    assert httpx.post(url, content=unknown, headers={"x-signature-256": sign(ctx.settings.tenant_webhook_secret("x"), unknown)}).status_code == 401
    assert httpx.post(url, content=b"not json").status_code == 401


def test_live_delivery_duplicate_reordered_and_dropped_events_converge(session_factory, provisioned, live_app):
    ctx, tenant, org, jobs = provisioned
    (jid, cvat_jid) = jobs[0]
    before = _inbox_count(session_factory, resource_id=cvat_jid)

    # 1) real signed delivery from CVAT -> inbox -> projection after refetch
    _patch(ctx, org, cvat_jid, state="in progress")
    _wait(lambda: _inbox_count(session_factory, resource_id=cvat_jid) > before)
    assert webhooks.process_inbox(session_factory, ctx) >= 1
    j = _job(session_factory, jid)
    assert j.cvat_state == "in progress" and j.synced_at is not None
    assert j.portal_state == "created", "CVAT observations must not change portal workflow state"

    # 2) duplicate: replay the exact stored payload with a valid signature -> deduped
    with session_factory() as s:
        row = s.scalars(select(WebhookInbox).where(WebhookInbox.event == "update:job", WebhookInbox.resource_id == cvat_jid).order_by(WebhookInbox.id.desc())).first()
        raw = json.dumps(row.payload).encode()
    n = _inbox_count(session_factory, resource_id=cvat_jid)
    r = httpx.post("http://localhost:8000/api/v1/labeling/webhooks/cvat", content=raw, headers={"x-signature-256": sign(ctx.settings.tenant_webhook_secret(tenant), raw)})
    with session_factory() as s2:
        keys = [(x.id, x.dedupe_key) for x in s2.scalars(select(WebhookInbox).where(WebhookInbox.resource_id == cvat_jid))]
    assert r.status_code == 202 and r.json()["status"] == "duplicate", keys and _inbox_count(session_factory, resource_id=cvat_jid) == n

    # 3) reordered: an older state arrives after a newer one; refetch means we still end on CVAT's current state
    _patch(ctx, org, cvat_jid, state="completed")
    _wait(lambda: _inbox_count(session_factory, resource_id=cvat_jid) > n)
    stale = json.loads(raw)  # the old "in progress" payload, re-signed, delivered late
    stale["job"]["updated_date"] = "2020-01-01T00:00:00.000000Z"
    stale_raw = json.dumps(stale).encode()
    assert httpx.post("http://localhost:8000/api/v1/labeling/webhooks/cvat", content=stale_raw,
                      headers={"x-signature-256": sign(ctx.settings.tenant_webhook_secret(tenant), stale_raw)}).status_code == 202
    while webhooks.process_inbox(session_factory, ctx):
        pass
    assert _job(session_factory, jid).cvat_state == "completed"

    # 4) dropped: receiver down while CVAT changes the other job; reconciliation converges without any webhook
    live_app.should_exit = True
    time.sleep(1.5)
    (_, other_cvat_jid) = jobs[1]
    _patch(ctx, org, other_cvat_jid, state="in progress")
    assert _job(session_factory, jobs[1][0]).cvat_state == "new"
    stats = reconcile.reconcile_tenant(session_factory, ctx, tenant)
    assert stats["checked"] == 2 and stats["changed"] >= 1 and stats["errors"] == 0
    j2 = _job(session_factory, jobs[1][0])
    assert j2.cvat_state == "in progress"
    assert (time.time() - j2.synced_at.timestamp()) < 30  # sync age is observable


def test_reconcile_records_out_of_band_deletion_without_failing(session_factory, provisioned):
    ctx, tenant, org, jobs = provisioned
    with session_factory() as s:
        task_id = s.scalar(select(LabelingJob.task_id).where(LabelingJob.id == jobs[0][0]))
        from api.labeling.models import LabelingTask
        cvat_task_id = s.get(LabelingTask, task_id).cvat_task_id
    ctx.cvat.delete_task(org.cvat_org_slug, cvat_task_id)
    stats = reconcile.reconcile_tenant(session_factory, ctx, tenant)
    assert stats["missing"] == 2
    assert _job(session_factory, jobs[0][0]).cvat_state == "missing"


def test_worker_tick_runs_inbox_and_reconcile(session_factory, provisioned):
    """The worker loop body: drains operations + inbox and reconciles on its interval (interval 0 => every tick)."""
    from dataclasses import replace

    from workers.run import tick

    ctx, tenant, org, jobs = provisioned
    ctx = replace(ctx, settings=replace(ctx.settings, reconcile_interval_s=0))
    _patch(ctx, org, jobs[0][1], state="in progress")
    assert _job(session_factory, jobs[0][0]).cvat_state == "new"
    assert tick(session_factory, ctx, last_reconcile=0.0) > 0
    assert _job(session_factory, jobs[0][0]).cvat_state == "in progress"
