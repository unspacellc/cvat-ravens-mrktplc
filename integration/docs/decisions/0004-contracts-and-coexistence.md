# 0004 — Contracts, identifiers and legacy correction-manifest coexistence

Status: accepted (Phase 0) · Date: 2026-10-28

## Decisions
- v1 contracts live in `contracts/` (JSON Schema 2020-12 + OpenAPI 3.1) and are enforced by `tests/contract`:
  `media_reference`, `frame_map`, `dataset_manifest`, `dataset_version_published_v1` (event), `error`, `openapi.yaml`.
- `error.schema.json` mirrors the marketplace error envelope (`error`, `statusCode`, `code`, `details`, `requestId`).
  Marketplace path conventions: `/api/v1` prefix, routers under `src/api/routers`, Alembic head at survey time `b7e2c4d6f810`
  (re-check at Phase 7 — it will have moved).
- Tenant = marketplace `customer_id`. Marketplace has no media/model catalog tables: `stored_files` (sha256, s3_key,
  status) is the media source of truth and `model_type` on `ProcessingRun` is the only model notion. The host adapters
  (`IdentityContext`, `MediaCatalog`, `ModelCatalog`, `DatasetPublisher`, `NotificationSink`) therefore bind to
  `stored_files`/Cognito-backed `CurrentUser`, and a **new model-registration source must be agreed before Phase 4**
  (open item; fixtures cover Phase 0–3).
- Frame identity: `FrameMap` (ffprobe pts) is persisted per clip; CVAT frame *i* = clip frame `start_frame + i*step`;
  tasks always created with `overlap=0` so a frame belongs to exactly one job.
- Outbox: reuse the marketplace `outbox_events` pattern (`aggregate_type`, `aggregate_id`, `event_type`, `payload`,
  `published_at`, `FOR UPDATE SKIP LOCKED`). The standalone build mirrors the same columns so incorporation is a table move.

## Legacy correction-manifest coexistence
Legacy: `ground-truth/{video_hash}/{model_type}/{model_type}_groundtruth_manifest_<ts>.json` keyed by (video, model).
- New dataset versions are written only under `labeling/{tenant}/{batch}/{version}/…` — a disjoint prefix, so they can
  never overwrite or shadow a legacy manifest (tested).
- Writing a legacy-shaped manifest is **opt-in per batch** (`api/labeling/legacy.py`), always a new timestamped key, never
  update/delete. Default off. The mapping from reviewed CVAT annotations to legacy `successes`/`failures` is **not yet
  defined** (depends on detection-manifest semantics from the ML side) — open item for Phase 3/7 with the marketplace owner.

## Model sources (owner, 2026-10-28)
Registered/custom models live in **SageMaker**; managed pre-labeling uses **Amazon Nova (Bedrock)** and **Rekognition**.
Those two are API services, not registry entries, so `ModelCatalog.resolve()` must return a `provider`
(`sagemaker` | `bedrock` | `rekognition`) plus provider-specific invocation config. Contract change lands in Phase 4
(`contracts/` model reference), not before.
