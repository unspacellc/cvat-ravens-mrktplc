from __future__ import annotations

import time
from pathlib import Path

from .conftest import CvatApi

LABELS = [{"name": "car", "type": "rectangle", "attributes": []}]


def wait_request(api: CvatApi, rq_id: str, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = api.get(f"/api/requests/{rq_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in ("finished", "failed"):
            return body
        time.sleep(1)
    raise TimeoutError(f"request {rq_id} did not finish")


def create_video_task(api: CvatApi, clip: Path, *, name="t", segment_size=40, overlap: int | None = 0, **data) -> dict:
    body = {"name": name, "labels": LABELS, "segment_size": segment_size}
    if overlap is not None:
        body["overlap"] = overlap
    r = api.post("/api/tasks", json=body)
    assert r.status_code == 201, r.text
    task = r.json()
    with clip.open("rb") as fh:
        r = api.post(
            f"/api/tasks/{task['id']}/data",
            data={"image_quality": 70, **{k: str(v) for k, v in data.items()}},
            files={"client_files[0]": (clip.name, fh, "video/mp4")},
        )
    assert r.status_code == 202, r.text
    rq = wait_request(api, r.json()["rq_id"])
    assert rq["status"] == "finished", rq
    return api.get(f"/api/tasks/{task['id']}").json()


def list_jobs(api: CvatApi, task_id: int) -> list[dict]:
    r = api.get("/api/jobs", params={"task_id": task_id, "type": "annotation"})
    assert r.status_code == 200, r.text
    return sorted(r.json()["results"], key=lambda j: j["start_frame"])
