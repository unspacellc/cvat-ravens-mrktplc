import hashlib
import hmac
import time

import pytest

from .helpers import create_video_task, list_jobs, wait_request

pytestmark = pytest.mark.capability

SECRET = "capability-secret"


def _wait_for(pred, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if (v := pred()):
            return v
        time.sleep(0.5)
    raise TimeoutError


def test_webhook_signature_over_raw_body(org, clip_cfr, webhook_receiver):
    api, _ = org
    r = api.post("/api/webhooks", json={
        "target_url": webhook_receiver.url, "type": "organization", "content_type": "application/json",
        "events": ["update:job", "update:task"], "secret": SECRET, "is_active": True, "enable_ssl": False,
    })
    assert r.status_code == 201, r.text
    hook_id = r.json()["id"]

    task = create_video_task(api, clip_cfr, segment_size=120)
    (job,) = list_jobs(api, task["id"])
    api.patch(f"/api/jobs/{job['id']}", json={"state": "in progress"})

    got = _wait_for(lambda: next((h for h in webhook_receiver.hooks if h.json().get("event") == "update:job"), None))
    expected = "sha256=" + hmac.new(SECRET.encode(), got.raw_body, hashlib.sha256).hexdigest()
    assert got.headers["x-signature-256"] == expected
    # Finding: no delivery-id / event headers; dedupe must be derived from the body.
    assert not [h for h in got.headers if "delivery" in h or h == "x-cvat-event"]
    body = got.json()
    assert body["webhook_id"] == hook_id and body["job"]["id"] == job["id"] and "updated_date" in body["job"]

    # ping works through the allowlisted receiver
    assert api.post(f"/api/webhooks/{hook_id}/ping").status_code == 200


def test_export_annotations_zip(org, clip_cfr):
    api, _ = org
    task = create_video_task(api, clip_cfr, segment_size=120)
    r = api.post(f"/api/tasks/{task['id']}/dataset/export", params={"format": "CVAT for video 1.1", "save_images": "false"})
    assert r.status_code == 202, r.text
    rq = wait_request(api, r.json()["rq_id"])
    assert rq["status"] == "finished", rq
    assert rq.get("result_url")
    dl = api.get(rq["result_url"].replace(api.http.base_url.__str__().rstrip("/"), ""), follow_redirects=True)
    assert dl.status_code == 200 and dl.content[:2] == b"PK"


def test_pat_and_org_scope(admin_token, org):
    api, slug = org
    from .conftest import CvatApi
    r = CvatApi(admin_token).post("/api/auth/access_tokens", json={"name": f"cap-{slug}", "read_only": True})
    assert r.status_code == 201, r.text
    pat = r.json()["value"]
    import httpx
    ro = httpx.Client(base_url=api.http.base_url, headers={"Authorization": f"Bearer {pat}", "X-Organization": slug})
    assert ro.get("/api/jobs").status_code == 200
    assert ro.post("/api/tasks", json={"name": "x", "labels": [{"name": "a"}]}).status_code in (401, 403)
