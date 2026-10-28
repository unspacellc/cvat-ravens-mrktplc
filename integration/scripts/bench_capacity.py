"""Measures ingestion, job-list latency and export duration on the running stack. Output: JSON on stdout.
Usage: uv run python scripts/bench_capacity.py [--seconds 60] [--size 1280x720] [--fps 30] [--segment 300]"""
import argparse
import json
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx

from tests.capability.conftest import ADMIN_PASSWORD, ADMIN_USER, BASE_URL, FIXTURE_DIR, CvatApi
from tests.capability.helpers import create_video_task, list_jobs, wait_request

ap = argparse.ArgumentParser()
ap.add_argument("--seconds", type=int, default=60); ap.add_argument("--size", default="1280x720")
ap.add_argument("--fps", type=int, default=30); ap.add_argument("--segment", type=int, default=300)
ap.add_argument("--polls", type=int, default=50)
a = ap.parse_args()

FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
clip = FIXTURE_DIR / f"bench_{a.size}_{a.seconds}s_{a.fps}fps.mp4"
if not clip.exists():
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={a.size}:rate={a.fps}:duration={a.seconds}",
                    "-pix_fmt", "yuv420p", "-g", "30", str(clip)], check=True)
key = httpx.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).json()["key"]
slug = f"b{uuid.uuid4().hex[:8]}"
CvatApi(key).post("/api/organizations", json={"slug": slug, "name": slug})
api = CvatApi(key, org=slug)

t0 = time.time(); task = create_video_task(api, clip, segment_size=a.segment); ingest_s = time.time() - t0
jobs = list_jobs(api, task["id"])
lat = []
for _ in range(a.polls):
    s = time.perf_counter(); api.get("/api/jobs", params={"task_id": task["id"]}); lat.append((time.perf_counter() - s) * 1000)
lat.sort()
t0 = time.time()
r = api.post(f"/api/tasks/{task['id']}/dataset/export", params={"format": "CVAT for video 1.1", "save_images": "false"})
wait_request(api, r.json()["rq_id"], timeout=600); export_s = time.time() - t0
print(json.dumps({"clip": clip.name, "bytes": clip.stat().st_size, "frames": task["size"], "jobs": len(jobs),
                  "ingest_s": round(ingest_s, 2), "export_s": round(export_s, 2),
                  "jobs_list_ms": {"p50": round(statistics.median(lat), 1), "p95": round(lat[int(len(lat) * .95) - 1], 1), "max": round(lat[-1], 1)}}))
