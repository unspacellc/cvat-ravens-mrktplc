"""Two tenants = two CVAT organizations owned by distinct users. Tenant B must not reach tenant A's data."""

import uuid

import httpx
import pytest

from .conftest import BASE_URL, CvatApi
from .helpers import create_video_task, list_jobs

pytestmark = pytest.mark.capability
PW = "Tmp-pass-12345!"


def _user():
    name = f"tn{uuid.uuid4().hex[:8]}"
    r = httpx.post(f"{BASE_URL}/api/auth/register", json={"username": name, "password1": PW, "password2": PW, "email": f"{name}@example.com"})
    assert r.status_code in (201, 204), r.text
    key = httpx.post(f"{BASE_URL}/api/auth/login", json={"username": name, "password": PW}).json()["key"]
    return name, key


def _tenant(clip):
    _, key = _user()
    slug = f"t{uuid.uuid4().hex[:10]}"
    assert CvatApi(key).post("/api/organizations", json={"slug": slug, "name": slug}).status_code == 201
    api = CvatApi(key, org=slug)
    task = create_video_task(api, clip, segment_size=120)
    (job,) = list_jobs(api, task["id"])
    return key, slug, task, job


def test_cross_tenant_access_is_denied_on_every_path(clip_cfr):
    a_key, a_slug, a_task, a_job = _tenant(clip_cfr)
    b_key, b_slug, _, _ = _tenant(clip_cfr)
    b = CvatApi(b_key)
    b_in_own_org = CvatApi(b_key, org=b_slug)

    for client in (b, b_in_own_org, CvatApi(b_key, org=a_slug)):
        for url in (f"/api/tasks/{a_task['id']}", f"/api/jobs/{a_job['id']}", f"/api/tasks/{a_task['id']}/data/meta",
                    f"/api/jobs/{a_job['id']}/annotations", f"/api/jobs/{a_job['id']}/data?type=frame&number=0&quality=compressed"):
            r = client.get(url)
            assert r.status_code in (403, 404), (url, r.status_code)
        assert client.patch(f"/api/jobs/{a_job['id']}", json={"state": "completed"}).status_code in (403, 404)
        assert client.request("DELETE", f"/api/tasks/{a_task['id']}").status_code in (403, 404)

    # B's listings never contain A's resources
    for client in (b, b_in_own_org):
        assert a_task["id"] not in [t["id"] for t in client.get("/api/tasks").json()["results"]]
        assert a_job["id"] not in [j["id"] for j in client.get("/api/jobs").json()["results"]]
    # sanity: A still sees its own
    assert CvatApi(a_key, org=a_slug).get(f"/api/tasks/{a_task['id']}").status_code == 200
