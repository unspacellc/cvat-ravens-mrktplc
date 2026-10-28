"""The frame map must agree with what CVAT actually serves (pixels), including VFR + nonzero offset + step."""

import io
import subprocess

import pytest
from PIL import Image, ImageChops, ImageStat

from api.labeling.framemap import build_frame_map

from .helpers import create_video_task, list_jobs

pytestmark = pytest.mark.capability


def _ffmpeg_frame(clip, n):
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(clip), "-vf", f"select=eq(n\\,{n})", "-fps_mode", "passthrough",
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], check=True, capture_output=True).stdout
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _cvat_frame(api, job_id, number):
    r = api.get(f"/api/jobs/{job_id}/data", params={"type": "frame", "number": number, "quality": "original"})
    assert r.status_code == 200, r.text
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def _mean_abs_diff(a, b):
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


@pytest.mark.parametrize("fixture,start,step", [("clip_cfr", 0, 1), ("clip_cfr", 10, 2), ("clip_vfr", 0, 1), ("clip_vfr", 5, 3)])
def test_cvat_frames_match_frame_map(request, org, fixture, start, step):
    clip = request.getfixturevalue(fixture)
    api, _ = org
    extra = {}
    if start:
        extra["start_frame"] = start
    if step > 1:
        extra["frame_filter"] = f"step={step}"
    task = create_video_task(api, clip, segment_size=1000, **extra)
    fm = build_frame_map(clip, start_frame=start, step=step)
    meta = api.get(f"/api/tasks/{task['id']}/data/meta").json()
    assert meta["size"] == fm.cvat_frame_count, "frame map disagrees with CVAT frame count"
    (job,) = list_jobs(api, task["id"])
    for cvat_n in {0, fm.cvat_frame_count // 2, fm.cvat_frame_count - 1}:
        got = _cvat_frame(api, job["id"], cvat_n)
        want = _ffmpeg_frame(clip, fm.cvat_to_clip(cvat_n))
        assert got.size == want.size
        assert _mean_abs_diff(got, want) < 6, f"{fixture} start={start} step={step} frame {cvat_n} misaligned"


def test_vfr_fixture_is_really_variable_and_offset(clip_vfr, clip_cfr):
    vfr, cfr = build_frame_map(clip_vfr), build_frame_map(clip_cfr)
    assert vfr.variable_frame_rate and not cfr.variable_frame_rate
    assert vfr.pts_s[0] > 1.0, "fixture should start at a nonzero timestamp"
