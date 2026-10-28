import pytest

from .helpers import create_video_task, list_jobs

pytestmark = pytest.mark.capability


def test_default_overlap_makes_jobs_overlap(org, clip_cfr):
    """Finding: omitting `overlap` yields overlapping jobs for video; the integration must send overlap=0."""
    api, _ = org
    task = create_video_task(api, clip_cfr, segment_size=40, overlap=None)
    jobs = list_jobs(api, task["id"])
    assert jobs[1]["start_frame"] < jobs[0]["stop_frame"] + 1


def test_cfr_clip_splits_into_jobs(org, clip_cfr):
    api, _ = org
    task = create_video_task(api, clip_cfr, segment_size=40)
    jobs = list_jobs(api, task["id"])
    assert [(j["start_frame"], j["stop_frame"]) for j in jobs] == [(0, 39), (40, 79), (80, 119)]
    meta = api.get(f"/api/tasks/{task['id']}/data/meta").json()
    assert meta["size"] == 120
    # Finding: video meta carries no per-frame timestamps; only a single `frames` entry.
    assert len(meta["frames"]) == 1
    assert "pts" not in meta["frames"][0] and "timestamp" not in meta["frames"][0]


def test_frame_filter_step_and_start_frame(org, clip_cfr):
    api, _ = org
    task = create_video_task(api, clip_cfr, segment_size=100, start_frame=10, frame_filter="step=2")
    meta = api.get(f"/api/tasks/{task['id']}/data/meta").json()
    assert meta["start_frame"] == 10 and meta["frame_filter"] == "step=2"
    # CVAT frame i <-> source frame start_frame + i*step
    assert meta["size"] == len(range(10, 120, 2))


def test_vfr_clip_ingests(org, clip_vfr):
    api, _ = org
    task = create_video_task(api, clip_vfr, segment_size=50)
    assert task["size"] > 0
