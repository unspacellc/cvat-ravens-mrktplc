# Capacity envelope and load fixture

Status: **targets proposed, one baseline measured** (Phase 0). Targets need owner sign-off; Phase 6 re-measures.

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
