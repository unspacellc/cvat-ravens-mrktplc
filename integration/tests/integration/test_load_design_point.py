"""Design-point load run: 5 labelers editing + 4 job creators ingesting at the same time, on the running stack.
Skipped unless LABELING_LOAD=1. Writes docs/phases/evidence/load-design-point-<date>.json and prints a summary.
Numbers describe THIS host only (see the environment block in the evidence); they are not production capacity."""

import json
import os
import platform
import statistics
import subprocess
import threading
import time
import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest

from api.labeling import fixtures as fx
from api.labeling.config import cvat_username_for
from api.labeling.models import LabelingJob, LabelingTask, TenantCvatOrg

from .conftest import CVAT_URL, FIXTURES, build_ctx, user_token
from .portal import LABELS, make_portal

pytestmark = [pytest.mark.capability, pytest.mark.load, pytest.mark.skipif(not os.environ.get("LABELING_LOAD"), reason="set LABELING_LOAD=1")]
EVIDENCE = Path(__file__).resolve().parents[2] / "docs" / "phases" / "evidence"
LABELERS, CREATORS, ROUNDS = 5, 4, int(os.environ.get("LABELING_LOAD_ROUNDS", "25"))
TARGETS_MS = {"job_list": 300, "editor_first_frame": 3000}
TARGETS_S = {"ingest_60s_clip": 60, "export_task": 30}


class Timings:
    def __init__(self):
        self._lock, self.ms, self.errors = threading.Lock(), {}, {}

    def timed(self, name, fn, *a, **kw):
        t = time.perf_counter()
        try:
            return fn(*a, **kw)
        except Exception:
            with self._lock:
                self.errors[name] = self.errors.get(name, 0) + 1
            raise
        finally:
            with self._lock:
                self.ms.setdefault(name, []).append((time.perf_counter() - t) * 1000)

    def summary(self):
        out = {}
        for k, v in sorted(self.ms.items()):
            v = sorted(v)
            out[k] = {"n": len(v), "p50": round(statistics.median(v), 1), "p95": round(v[max(0, int(len(v) * 0.95 + 0.5) - 1)], 1), "max": round(v[-1], 1),
                      "errors": self.errors.get(k, 0)}
        return out


def _docker_stats(stop, peaks):
    while not stop.is_set():
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            name, cpu, mem = line.split("|")
            used = mem.split("/")[0].strip()
            mib = float(used[:-3]) * (1024 if used.endswith("GiB") else 1) if used.endswith(("MiB", "GiB")) else 0.0
            c = peaks.setdefault(name, {"cpu_pct": 0.0, "mem_mib": 0.0})
            c["cpu_pct"] = max(c["cpu_pct"], float(cpu.rstrip("%") or 0))
            c["mem_mib"] = max(c["mem_mib"], round(mib, 1))
        stop.wait(2)


def test_design_point(session_factory, cvat_token, tenant_ids):
    ta, _ = tenant_ids
    u = uuid.uuid4().hex[:6]
    clip = FIXTURES / "load_1280x720_60s_30fps.mp4"
    if not clip.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=60", "-pix_fmt", "yuv420p", "-g", "30", str(clip)], check=True)
    media = {(ta, f"clip{i}-{ta}"): fx.make_media_ref(clip, f"clip{i}-{ta}", root=FIXTURES, width=1280, height=720) for i in range(CREATORS + 1)}
    ctx = build_ctx(cvat_token, media)
    ctx.settings = replace(ctx.settings, segment_size=300)  # the capacity fixture: 300-frame segments -> 6 jobs per 60 s clip
    portal = make_portal(session_factory, ctx)
    admin, reviewer = f"adm-{u}", f"rev-{u}"
    labelers = [f"lab{i}-{u}" for i in range(LABELERS)]
    creators = [f"cre{i}-{u}" for i in range(CREATORS)]
    portal.add_member(ta, admin, "admin")
    portal.add_member(ta, reviewer, "reviewer")
    for s in labelers:
        portal.add_member(ta, s, "labeler")
    for s in creators:
        portal.add_member(ta, s, "creator")
    t = Timings()
    schema_id = portal.call("POST", "/schemas", admin, json={"labels": LABELS}).json()["id"]

    # --- batch the labelers will work in (also the single-clip ingestion measurement, no contention)
    t0 = time.perf_counter()
    r = portal.call("POST", "/batches", admin, json={"name": "work", "asset_ids": [f"clip0-{ta}"], "schema_version_id": schema_id}, headers={"Idempotency-Key": f"work-{u}"})
    portal.run_operations(r.json()["operation_id"])
    ingest_alone_s = time.perf_counter() - t0
    jobs = portal.call("GET", "/jobs", admin, params={"limit": 200}).json()["items"]
    assert len(jobs) == 6
    for lab, job in zip(labelers, jobs, strict=False):  # 6 jobs, 5 labelers: the sixth stays unassigned
        assert portal.call("POST", f"/jobs/{job['id']}/assignment", admin, json={"user_id": lab, "expected_revision": job["revision"]}).status_code == 200
    with session_factory() as s:
        slug = s.get(TenantCvatOrg, (ta, ctx.settings.cvat_instance)).cvat_org_slug
        rows = {j.id.hex: (j.cvat_job_id, j.task_id) for j in s.query(LabelingJob).filter(LabelingJob.tenant_id == ta)}
        label_ids = ctx.cvat.task_labels(slug, s.get(LabelingTask, next(iter(rows.values()))[1]).cvat_task_id)
        task_cvat_id = s.get(LabelingTask, next(iter(rows.values()))[1]).cvat_task_id
    label_id = next(iter(label_ids))
    tokens = {lab: user_token(cvat_username_for(lab)) for lab in labelers}

    ingest_contended, export_s, worker_errors = [], {}, []
    stop_stats, peaks = threading.Event(), {}
    threading.Thread(target=_docker_stats, args=(stop_stats, peaks), daemon=True).start()
    creators_done = threading.Event()

    def creator(i, sub):
        t.timed("media_list", portal.call, "GET", "/media", sub)
        t.timed("batch_preview", portal.call, "POST", "/batches/preview", sub, json={"asset_ids": [f"clip{i + 1}-{ta}"]})
        t0 = time.perf_counter()
        r = t.timed("batch_create", portal.call, "POST", "/batches", sub, json={"name": f"c{i}", "asset_ids": [f"clip{i + 1}-{ta}"], "schema_version_id": schema_id},
                    headers={"Idempotency-Key": f"cre-{u}-{i}"})
        portal.run_operations(r.json()["operation_id"])
        ingest_contended.append(time.perf_counter() - t0)

    def labeler(lab, job):
        cvat_job, _ = rows[job["id"].replace("-", "")]
        h = {"Authorization": f"Token {tokens[lab]}", "X-Organization": slug}
        with httpx.Client(base_url=CVAT_URL, headers=h, timeout=120) as c:
            for k in range(ROUNDS):
                t.timed("job_list", portal.call, "GET", "/jobs", lab)
                t.timed("editor_launch", portal.call, "POST", f"/jobs/{job['id']}/editor-launch", lab)
                t0 = time.perf_counter()  # what the editor needs before the first frame is usable
                for path, params in ((f"/api/jobs/{cvat_job}", None), (f"/api/jobs/{cvat_job}/data/meta", None),
                                     (f"/api/jobs/{cvat_job}/data", {"type": "chunk", "index": 0, "quality": "compressed"}), (f"/api/jobs/{cvat_job}/annotations", None)):
                    assert c.get(path, params=params).status_code == 200, path
                with t._lock:
                    t.ms.setdefault("editor_first_frame", []).append((time.perf_counter() - t0) * 1000)
                shapes = [{"type": "rectangle", "frame": 0, "label_id": label_id, "points": [10 + k, 10, 90 + k, 90], "occluded": False, "attributes": [], "source": "manual", "z_order": 0, "rotation": 0, "outside": False}]
                t.timed("annotation_save", lambda body={"version": 0, "tags": [], "tracks": [], "shapes": shapes}: c.put(f"/api/jobs/{cvat_job}/annotations", json=body).raise_for_status())
                time.sleep(1.0)  # think time between editor loads
            while not creators_done.is_set():  # keep the editor traffic going while the creators finish ingesting
                t.timed("job_list", portal.call, "GET", "/jobs", lab)
                time.sleep(1)

    def board_watcher():
        while not creators_done.is_set():
            t.timed("board_list_admin", portal.call, "GET", "/jobs", admin, params={"limit": 100})
            t.timed("metrics_overview", portal.call, "GET", "/metrics/overview", admin)
            time.sleep(1)

    def guarded(fn):
        def run(*a):
            try:
                fn(*a)
            except BaseException as e:  # a dead worker would silently shrink the load, so it must fail the run
                worker_errors.append(f"{fn.__name__}{a[:1]}: {type(e).__name__}: {e}")
        return run

    threads = [threading.Thread(target=guarded(creator), args=(i, s)) for i, s in enumerate(creators)]
    threads += [threading.Thread(target=guarded(labeler), args=(lab, job)) for lab, job in zip(labelers, jobs, strict=False)]
    watcher = threading.Thread(target=guarded(board_watcher))
    t_start = time.perf_counter()
    for th in threads + [watcher]:
        th.start()
    for th in threads[:CREATORS]:
        th.join()
    creators_done.set()
    for th in threads[CREATORS:] + [watcher]:
        th.join()
    wall_s = time.perf_counter() - t_start

    # export of the annotated task (CVAT for video 1.1, annotations only)
    t0 = time.perf_counter()
    ctx.cvat.export_task_native(slug, task_cvat_id)
    export_s["annotated_task"] = round(time.perf_counter() - t0, 2)
    stop_stats.set()

    assert not worker_errors, worker_errors
    s = t.summary()
    assert s["editor_first_frame"]["n"] == LABELERS * ROUNDS and len(ingest_contended) == CREATORS, "load shrank: workers did not finish"
    ingest_contended.sort()
    result = {
        "date": date.today().isoformat(),
        "environment": {"host": platform.platform(), "docker": subprocess.run(["docker", "info", "--format", "{{.NCPU}} CPU / {{.MemTotal}} bytes / {{.Architecture}}"], capture_output=True, text=True).stdout.strip(),
                        "note": "Mac Docker Desktop; CVAT images are linux/amd64 under emulation. NOT a production-representative host."},
        "fixture": {"clip": "1280x720, 30 fps, 60 s (1800 frames)", "segment_size": 300, "jobs_per_clip": 6, "labelers": LABELERS, "creators": CREATORS, "rounds_per_labeler": ROUNDS},
        "wall_s": round(wall_s, 1),
        "latency_ms": s,
        "ingest_60s_clip_s": {"alone": round(ingest_alone_s, 1), "with_4_concurrent_creators_and_5_labelers": [round(x, 1) for x in ingest_contended]},
        "export_s": export_s,
        "peaks_during_run": {k: v for k, v in sorted(peaks.items()) if k.startswith(("cvat", "labeling", "traefik"))},
        "targets": {"job_list_p95_ms": TARGETS_MS["job_list"], "editor_first_frame_p95_ms": TARGETS_MS["editor_first_frame"],
                    "ingest_60s_clip_s": TARGETS_S["ingest_60s_clip"], "export_task_s": TARGETS_S["export_task"]},
    }
    result["verdict"] = {
        "job_list_p95": s["job_list"]["p95"] <= TARGETS_MS["job_list"], "editor_first_frame_p95": s["editor_first_frame"]["p95"] <= TARGETS_MS["editor_first_frame"],
        "ingest_under_load_max": ingest_contended[-1] <= TARGETS_S["ingest_60s_clip"], "export": export_s["annotated_task"] <= TARGETS_S["export_task"],
        "zero_errors": not any(v["errors"] for v in s.values()),
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"load-design-point-{result['date']}.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    assert result["verdict"]["zero_errors"], "requests failed under load"
