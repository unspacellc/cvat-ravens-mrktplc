# Capacity envelope and load fixture

Status: **targets proposed (need owner sign-off); baseline measured (Phase 0); design-point run on the local host measured (Phase 6, bottom of this file)**. Production-host numbers are still unmeasured.

## Load fixture (proposed)
| Dimension | Target |
| --- | --- |
| Clip | 1280×720 (also test 1920×1080), 30 fps, 60 s (1 800 frames); stretch 10 min |
| Segmentation | `segment_size=300` frames, `overlap=0` → 6 jobs/clip |
| Batch | 10 clips (60 jobs); stretch 50 clips |
| Concurrent users (owner, 2026-10-28) | **5 labelers editing** + **2 job creators** (best case) up to **4 job creators** (worst case), at one time. Treat 5 + 4 = 9 simultaneous active users as the design point. Reviewers/admin not specified |
| Latency targets (p95) | job list API < 300 ms; editor first-frame load < 3 s; task ingestion (60 s clip) < 60 s; export (task) < 30 s |
| Recovery objectives | webhook→projection ≤ 5 min (reconcile interval); RPO ≤ 15 min (DB), RTO ≤ 2 h (Phase 1 restore drill measures) |
| Cost ceiling (owner, 2026-10-28) | **Whole system < $4,000/month** (current spend ~$2.5–2.8k at ~5 users); minimize as users grow. Confirmed: $4,000 is the whole system, so labeling headroom is **~$1,200/month**. Proposed sub-limits (not yet confirmed): pre-label spend ≤ $300/month and ≤ $50/batch by default; budget alarm at 75%; CVAT host scheduled off-hours. Estimates only until Phase 6 measures real usage |

## Baseline (single run, `scripts/bench_capacity.py`, 2026-10-28)
Environment: macOS arm64 host, Docker Desktop 8 CPU / ~15.6 GiB, **amd64 images under emulation**, full CVAT stack incl. analytics.
Clip: synthetic `testsrc2` 1280×720, 60 s, 30 fps, 24.3 MB.

| Metric | Result |
| --- | --- |
| Frames / jobs | 1 800 / 6 |
| Task ingestion (create + upload + chunking) | 3.4 s |
| Task annotation export (empty, no images) | 1.1 s |
| `GET /api/jobs` latency, 50 sequential calls | p50 25 ms · p95 27 ms · max 40 ms |
| Idle-ish memory | server 592 MiB, import worker 463 MiB, kvrocks 145 MiB, postgres 91 MiB |

## Caveats — do not treat as capacity evidence
- One run, one machine, no concurrency, empty annotations, synthetic content (compresses unlike real footage), emulated CPU.
- Video decode throughput under real editing load, export of annotated tasks with images, disk high-water mark, and
  concurrent-user latency are **not yet measured** (Phase 6 on a representative CPU host: record instance type).
- CVAT image is `linux/amd64` only; production CPU host should be amd64 (EC2) so numbers there will differ.

## Phase 6 measurement — design point, local host (2026-10-30)
`LABELING_LOAD=1 uv run pytest tests/integration/test_load_design_point.py -s` → `docs/phases/evidence/load-design-point-2026-10-30.json`.
Fixture: 1280×720, 30 fps, 60 s clip, 300-frame segments (6 jobs/clip); **5 labelers** each doing 25 editor loads + saves (1 s think time), **4 creators** each creating a batch and ingesting a 60 s clip, and an admin polling the board and metrics, all at once (33.8 s wall). Same host as the Phase 0 baseline (Docker Desktop, 8 CPU, amd64 images under emulation) — **not production-representative**.

| Operation | n | p50 ms | p95 ms | max ms |
| --- | --- | --- | --- | --- |
| Job list (labelers, portal API) | 125 | 12.4 | 19.3 | 52.4 |
| Board list (admin, 100 rows) | 12 | 22.7 | 34.9 | 52.9 |
| Editor launch (portal) | 125 | 43.7 | 66.1 | 97.1 |
| Editor first load: job + meta + first chunk + annotations (CVAT) | 125 | 148.2 | 217.2 | 1616.8 |
| Annotation save (CVAT) | 125 | 78.0 | 108.8 | 146.0 |
| Metrics overview | 12 | 13.1 | 19.4 | 40.5 |
| Media list | 4 | 65.1 | 74.0 | 74.0 |
| Batch preview | 4 | 12.8 | 16.6 | 16.6 |
| Batch create (202) | 4 | 40.4 | 45.7 | 45.7 |

| Metric | Target | Measured |
| --- | --- | --- |
| Job list p95 | < 300 ms | 19.3 ms |
| Editor first load p95 | < 3 s | 217.2 ms (max 1616.8 ms) |
| Ingest 60 s clip | < 60 s | 6.0 s alone; 7.3–11.5 s with 4 concurrent ingests and 5 labelers |
| Export (task, annotated) | < 30 s | 1.08 s (one shape per job — see caveats) |
| Request errors | 0 | 0 of 536 |

Peak CPU (% of one core): cvat_worker_import 462, cvat_worker_webhooks 94, cvat_worker_export 71, cvat_server 65. Peak memory (MiB): cvat_server 985, cvat_clickhouse 802, cvat_worker_import 596, cvat_worker_export 522.

**Caveats — do not extrapolate.** Synthetic video; one annotation per job so export and annotation-save are far lighter than real work; the labelers are scripted at a fixed pace, not people scrubbing video (frame-by-frame chunk traffic is not modeled beyond the first chunk); 33.8 s is a short window; disk high-water mark, export with images, and AWS usage were **not measured** (no pre-labeling this phase); the 8 CPUs are shared with the load generator itself.
