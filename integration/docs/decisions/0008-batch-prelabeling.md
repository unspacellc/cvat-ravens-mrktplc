# 0008 — Batch pre-labeling orchestration

Status: accepted for the offline/fake path; **real AWS path unverified** · Date: 2026-10-28

## Decisions
- **Backend contract** (`inference.py`): `submit` (idempotent on a deterministic `inference_id`), `poll`, `cancel`. The
  SageMaker async adapter (S3 staging + `InvokeEndpointAsync` with `InferenceId`, completion = object at the output or
  failure location) and a pixel-detecting offline fake satisfy it, so orchestration is tested once. Wire formats are
  versioned JSON schemas (`contracts/schemas/prelabel_request|response.schema.json`); boxes are in *model input* pixels.
- **Providers.** `ModelCatalog` descriptors carry `provider` (`sagemaker_async` | `bedrock_nova` | `rekognition`) and the pinned
  artifact identity, class mapping and preprocessing. Only `sagemaker_async` is implemented; others are refused up front
  (422) and shown as unsupported in `GET /models`. Nova and Rekognition are API services and need their own adapters.
- **Alignment.** CVAT frame index ↔ clip frame via the stored FrameMap; frames are decoded with `-fps_mode passthrough` and the
  decoded count must equal the frame map or the run fails (never guessed). Letterbox/stretch transforms derive scale from
  the *pasted* size so forward/inverse are exact; out-of-bounds boxes after inversion are dropped as invalid, not clamped.
  Frame ids are `<task id>:<cvat frame>` and the response must cover exactly the requested ids.
- **Validation is strict.** A malformed/incomplete response is a failed attempt (bounded retries, new inference id per
  attempt/epoch), never an empty result. Unknown labels, low confidence and invalid boxes are dropped and *counted*.
- **Import once.** `prelabel_import` ledger per (run, task); per job it records the post-import annotation hash. Before any
  write the live job is read: empty ⇒ write then read back and compare frames/coordinates/labels; identical to our shapes ⇒
  adopt (ambiguous timeout); anything else ⇒ `conflict`, nothing overwritten, run fails non-retryably. Pre-labeling is only
  allowed while every job is untouched (`created`), and a batch can have at most one completed run (later edits ⇒ revision batch).
- **Gate.** While a run is `queued|running`, assignment and editor launch are refused (409 `prelabel_in_progress`).
  Failure, cancellation and manual fallback release the gate; success releases it after verified import.
- **Three outcomes stay distinct**: `failed` (requests failed / import refused), `succeeded` with `zero_detection=true`
  (model ran and found nothing), `cancelled`/`manual_fallback` (explicit human choices). Late results of a stopped run are discarded.
- **Bounded queueing.** `max_inflight` submitted chunks per run (default 4), `frames_per_request` (default 8), `frame_stride`
  for sampled prediction. Waiting is `operations.Pending`: re-queue without consuming an attempt.
- **Metrics** (`metrics.py`): `iou-greedy` v1 correction metrics from the stored baseline vs the *approved* snapshot, ambiguity
  counted not guessed, `None` for a zero denominator, limitations returned with every result.

## Not done / limits
- No real SageMaker endpoint, Nova or Rekognition call has been made. The adapter is tested against fake boto clients only.
- Frames are decoded from the local clip per tick (fixture `ClipSource`); production should stream from S3 with bounded memory.
- Nuclio/in-editor interactive models, Batch Transform and multi-model ensembles are out of scope.
- Rectangles only (no polygons/masks/tracks).
