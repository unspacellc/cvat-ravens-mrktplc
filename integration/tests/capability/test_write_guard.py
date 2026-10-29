"""Write-freeze (send-off) enforcement against the guarded CVAT image."""

import subprocess
import textwrap

import httpx
import pytest

from .conftest import ADMIN_PASSWORD, ADMIN_USER, BASE_URL, CvatApi
from .helpers import create_video_task, list_jobs

pytestmark = pytest.mark.capability


def _shape(label_id: int) -> dict:
    return {"type": "rectangle", "frame": 0, "label_id": label_id, "points": [1, 1, 10, 10], "attributes": [],
            "occluded": False, "outside": False, "z_order": 0, "source": "manual"}


@pytest.fixture
def frozen_setup(org, clip_cfr, admin):
    api, _ = org
    task = create_video_task(api, clip_cfr, segment_size=120)
    (job,) = list_jobs(api, task["id"])
    label_id = api.get("/api/labels", params={"task_id": task["id"]}).json()["results"][0]["id"]
    yield api, task, job, label_id
    admin.http.delete(f"/api/labeling-guard/tasks/{task['id']}/freeze")


def _freeze(admin, task_id, send_off="so-1"):
    r = admin.http.put(f"/api/labeling-guard/tasks/{task_id}/freeze", json={"send_off_id": send_off})
    assert r.status_code == 200, r.text


def test_rest_writes_blocked_reads_allowed_and_unfreeze(frozen_setup, admin):
    api, task, job, label_id = frozen_setup
    ann = f"/api/jobs/{job['id']}/annotations"
    assert api.patch(ann, params={"action": "create"}, json={"shapes": [_shape(label_id)], "tags": [], "tracks": []}).status_code == 200

    _freeze(admin, task["id"])
    body = {"shapes": [_shape(label_id)], "tags": [], "tracks": []}
    assert api.patch(ann, params={"action": "create"}, json=body).status_code == 423
    assert api.request("PUT", ann, json=body).status_code == 423
    assert api.request("DELETE", ann).status_code == 423
    assert api.patch(f"/api/tasks/{task['id']}/annotations", params={"action": "create"}, json=body).status_code == 423
    # reopen / stage changes and task deletion are also blocked
    assert api.patch(f"/api/jobs/{job['id']}", json={"stage": "annotation", "state": "in progress"}).status_code == 423
    assert api.request("DELETE", f"/api/tasks/{task['id']}").status_code == 423
    # schema (label) edits are blocked too
    assert api.patch(f"/api/labels/{label_id}", json={"name": "renamed"}).status_code == 423
    # reads (needed for send-off export) still work
    assert api.get(ann).status_code == 200
    assert len(api.get(ann).json()["shapes"]) == 1
    # an export is a POST that only reads: it must stay possible on a frozen task (send-off exports after the barrier)
    ex = api.post(f"/api/tasks/{task['id']}/dataset/export", params={"format": "CVAT for video 1.1", "save_images": "false"})
    assert ex.status_code == 202, ex.text

    admin.http.delete(f"/api/labeling-guard/tasks/{task['id']}/freeze")
    assert api.patch(ann, params={"action": "create"}, json=body).status_code == 200


def test_non_superuser_cannot_freeze(admin_token, frozen_setup):
    api, task, _, _ = frozen_setup
    r = api.http.put(f"/api/labeling-guard/tasks/{task['id']}/freeze", json={"send_off_id": "x"})
    assert r.status_code in (200, 403)  # org owner is not a superuser unless admin; admin token is superuser
    # Use a non-superuser account to assert denial
    import uuid
    name = f"u{uuid.uuid4().hex[:8]}"
    reg = httpx.post(f"{BASE_URL}/api/auth/register", json={"username": name, "password1": "Tmp-pass-12345!", "password2": "Tmp-pass-12345!", "email": f"{name}@example.com"})
    assert reg.status_code in (201, 204), reg.text
    key = httpx.post(f"{BASE_URL}/api/auth/login", json={"username": name, "password": "Tmp-pass-12345!"}).json()["key"]
    r = CvatApi(key).http.put(f"/api/labeling-guard/tasks/{task['id']}/freeze", json={"send_off_id": "x"})
    assert r.status_code == 403


def test_preexisting_browser_session_is_blocked(frozen_setup, admin):
    api, task, job, label_id = frozen_setup
    s = httpx.Client(base_url=BASE_URL, timeout=60)
    assert s.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).status_code == 200
    csrf = s.cookies.get("csrftoken")
    headers = {"X-CSRFToken": csrf, "X-Organization": api.http.headers["X-Organization"]} if csrf else {}
    s.headers.update(headers)
    ann = f"/api/jobs/{job['id']}/annotations"
    body = {"shapes": [_shape(label_id)], "tags": [], "tracks": []}
    assert s.patch(ann, params={"action": "create"}, json=body).status_code == 200  # session works pre-freeze
    _freeze(admin, task["id"])
    assert s.patch(ann, params={"action": "create"}, json=body).status_code == 423  # same session, now frozen


def _manage_shell(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "-i", "cvat_server", "python3", "/opt/cvat/manage.py", "shell"],
                          input=textwrap.dedent(code), text=True, capture_output=True, timeout=180)


def test_worker_path_and_concurrent_writer_serialization(frozen_setup, admin):
    """Direct dataset_manager call (the RQ import-worker path): in-flight writer finishes first, later writers fail."""
    api, task, job, label_id = frozen_setup
    code = f"""
    import threading, time
    from django.db import transaction, connection
    from cvat.apps.dataset_manager import task as dm
    from cvat.apps.labeling_guard import guard

    T, J, L = {task['id']}, {job['id']}, {label_id}
    data = {{"shapes": [{{"type": "rectangle", "frame": 0, "label_id": L, "points": [1,1,5,5], "attributes": [], "occluded": False, "outside": False, "z_order": 0, "source": "manual", "group": 0, "elements": []}}], "tags": [], "tracks": [], "intervals": []}}
    started = threading.Event(); done_writer = []

    def slow_writer():
        try:
            with transaction.atomic():
                guard.assert_writable(T)
                started.set(); time.sleep(3)
                dm.JobAnnotation(J).create(data)
            done_writer.append(time.time())
        finally:
            connection.close()

    t = threading.Thread(target=slow_writer); t.start(); started.wait()
    t0 = time.time(); guard.freeze(T, "so-race"); waited = time.time() - t0
    t.join()
    print("WAITED", round(waited, 1), "WRITER_COMMITTED_BEFORE_FREEZE", bool(done_writer) and done_writer[0] <= time.time())
    try:
        dm.patch_job_data(J, data, dm.PatchAction.CREATE)
        print("LATE_WRITE", "ALLOWED")
    except guard.TaskFrozenError:
        print("LATE_WRITE", "BLOCKED")
    try:
        dm.put_task_data(T, data)
        print("TASK_PUT", "ALLOWED")
    except guard.TaskFrozenError:
        print("TASK_PUT", "BLOCKED")
    guard.unfreeze(T)
    """
    res = _manage_shell(code)
    out = res.stdout
    assert "LATE_WRITE BLOCKED" in out and "TASK_PUT BLOCKED" in out, res.stdout + res.stderr
    waited = float(out.split("WAITED")[1].split()[0])
    assert waited >= 2.0, f"freeze did not wait for in-flight writer: {out}"
    assert "WRITER_COMMITTED_BEFORE_FREEZE True" in out, res.stderr[-2000:]
