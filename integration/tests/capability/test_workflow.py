import pytest

from .helpers import create_video_task, list_jobs

pytestmark = pytest.mark.capability

# Portal status -> proposed CVAT (stage, state) from the plan table.
PAIRS = [
    ("annotation", "new"),
    ("annotation", "in progress"),
    ("validation", "new"),
    ("validation", "in progress"),
    ("annotation", "rejected"),
    ("acceptance", "completed"),
]


def test_stage_state_pairs_and_assignee(org, clip_cfr):
    api, _ = org
    task = create_video_task(api, clip_cfr, segment_size=120)
    (job,) = list_jobs(api, task["id"])
    me = api.get("/api/users/self").json()["id"]

    r = api.patch(f"/api/jobs/{job['id']}", json={"assignee": me})
    assert r.status_code == 200 and r.json()["assignee"]["id"] == me

    for stage, state in PAIRS:
        r = api.patch(f"/api/jobs/{job['id']}", json={"stage": stage, "state": state})
        assert r.status_code == 200, (stage, state, r.text)
        got = r.json()
        assert (got["stage"], got["state"]) == (stage, state)


def test_stage_change_alone_resets_state(org, clip_cfr):
    api, _ = org
    task = create_video_task(api, clip_cfr, segment_size=120)
    (job,) = list_jobs(api, task["id"])
    api.patch(f"/api/jobs/{job['id']}", json={"state": "in progress"})
    got = api.patch(f"/api/jobs/{job['id']}", json={"stage": "validation"}).json()
    assert got["state"] == "new"


def test_issue_create_and_resolve(org, clip_cfr):
    api, _ = org
    task = create_video_task(api, clip_cfr, segment_size=120)
    (job,) = list_jobs(api, task["id"])
    me = api.get("/api/users/self").json()["id"]
    r = api.post("/api/issues", json={"frame": 3, "position": [1, 1, 20, 20], "job": job["id"], "message": "bad box", "assignee": me})
    assert r.status_code == 201, r.text
    issue = r.json()
    assert issue["resolved"] is False
    r = api.patch(f"/api/issues/{issue['id']}", json={"resolved": True})
    assert r.status_code == 200 and r.json()["resolved"] is True
