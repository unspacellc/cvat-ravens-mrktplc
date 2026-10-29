import uuid
from dataclasses import replace

import pytest

from api.labeling import fixtures as fx
from api.labeling.inference import FakeBackend

from .conftest import FIXTURES, build_ctx
from .pixel_clip import make_pixel_clip
from .portal import make_batch, make_portal
from .test_prelabel import DESC

pytestmark = pytest.mark.capability


@pytest.fixture
def w(session_factory, cvat_token, clips, tenant_ids):
    ta, tb = tenant_ids
    u = uuid.uuid4().hex[:6]
    asset = f"a-{ta}"
    ctx = build_ctx(cvat_token, {(ta, asset): fx.make_media_ref(make_pixel_clip(FIXTURES), asset, root=FIXTURES, clip_start_offset_s=12.5)})
    ctx.settings = replace(ctx.settings, segment_size=40)
    ctx.inference = FakeBackend()
    ctx.models = fx.FixtureModels({ta: {"m1": DESC}})
    portal = make_portal(session_factory, ctx)
    out = {"p": portal, "ta": ta, "tb": tb, "adm": f"adm-{u}", "lab": f"lab-{u}", "adm_b": f"admb-{u}"}
    portal.add_member(ta, out["adm"], "admin")
    portal.add_member(ta, out["lab"], "labeler")
    portal.add_member(tb, out["adm_b"], "admin")
    out["batch"] = make_batch(portal, ta, out["adm"], asset)["batch_id"]
    out["jobs"] = portal.call("GET", "/jobs", out["adm"]).json()["items"]
    return out


def test_prelabel_over_http(w):
    p = w["p"]
    hdr = {"Idempotency-Key": "pl-1"}
    path = f"/batches/{w['batch']}/prelabel-runs"
    assert p.call("POST", path, w["lab"], json={"model_id": "m1"}, headers=hdr).status_code == 403  # labelers cannot start runs
    assert p.call("POST", path, w["adm_b"], tenant=w["tb"], json={"model_id": "m1"}, headers=hdr).status_code == 404  # other tenant
    nope = p.call("POST", path, w["adm"], json={"model_id": "other"}, headers={"Idempotency-Key": "pl-x"})
    assert nope.status_code == 403 and nope.json()["code"] == "model_not_entitled"
    assert p.call("POST", path, w["adm"], json={"model_id": "m1", "frame_stride": 0}, headers={"Idempotency-Key": "pl-y"}).status_code == 422
    ok = p.call("POST", path, w["adm"], json={"model_id": "m1", "frames_per_request": 16}, headers=hdr)
    assert ok.status_code == 202, ok.text
    run_id, op_id = ok.json()["run_id"], ok.json()["operation_id"]
    assert p.call("POST", path, w["adm"], json={"model_id": "m1", "frames_per_request": 16}, headers=hdr).json() == ok.json()  # replay
    # annotation is blocked while the run is active, with a specific code
    j = w["jobs"][0]
    blocked = p.call("POST", f"/jobs/{j['id']}/assignment", w["adm"], json={"user_id": w["lab"], "expected_revision": j["revision"]})
    assert blocked.status_code == 409 and blocked.json()["code"] == "prelabel_in_progress"
    assert p.call("GET", f"/batches/{w['batch']}", w["adm"]).json()["prelabel"]["status"] in ("queued", "running")

    h_runs = 0
    from api.labeling import operations, prelabel
    handlers = {"prelabel_run": prelabel.prelabel_run(p.ctx)}
    from api.labeling.models import Operation
    while h_runs < 200:
        h_runs += 1
        operations.run_one(p.session_factory, handlers, op_id=uuid.UUID(op_id))
        with p.session_factory() as s:
            op = s.get(Operation, uuid.UUID(op_id))
            if op.state in operations.TERMINAL:
                break
            op.run_after = operations.now()
            s.commit()
    run = p.call("GET", f"/prelabel-runs/{run_id}", w["adm"]).json()
    assert run["status"] == "succeeded" and run["summary"]["zero_detection"] is False and run["model"]["version"] == "1.0.0"
    assert p.call("GET", f"/prelabel-runs/{run_id}", w["adm_b"], tenant=w["tb"]).status_code == 404
    m = p.call("GET", f"/prelabel-runs/{run_id}/metrics", w["adm"]).json()
    assert m["jobs_measured"] == 0 and m["method"]["name"] == "iou-greedy" and m["limitations"]
    assert p.call("POST", f"/prelabel-runs/{run_id}/cancel", w["adm"]).status_code == 409  # already finished
    ok2 = p.call("POST", f"/jobs/{j['id']}/assignment", w["adm"], json={"user_id": w["lab"], "expected_revision": j["revision"]})
    assert ok2.status_code == 200


def test_model_listing_is_tenant_scoped_and_flags_unsupported_providers(w):
    p = w["p"]
    p.ctx.models = fx.FixtureModels({w["ta"]: {"m1": DESC, "nova": {**DESC, "provider": "bedrock_nova"}}})
    items = {m["model_id"]: m for m in p.call("GET", "/models", w["adm"]).json()["items"]}
    assert items["m1"]["supported"] is True and items["nova"]["supported"] is False and "artifact" not in items["m1"]
    assert p.call("GET", "/models", w["adm_b"], tenant=w["tb"]).json()["items"] == []
    assert p.call("GET", "/models", w["lab"]).status_code == 403
