# 0001 — CVAT version pin and capability matrix

Status: accepted (Phase 0) · Date: 2026-10-28

## Decision
Pin **CVAT v2.80.0** (`bb0a873fe`, tag `v2.80.0`). The repo checkout (`a415be662`, `2.80.1-alpha`) is an unreleased
dev build and is *not* used; the implementation builds from a git worktree of the tag (`integration/.cvat-src`, gitignored).

| Artifact | Pin |
| --- | --- |
| cvat/server | `v2.80.0` `sha256:955ae3b34089fbd57d22bf9a692cef82c99647f9970dd68a716448eec252f425` |
| cvat/ui | `v2.80.0` `sha256:a041c140176dd8e595fe9b40c165c9f2e9a785861ce04d64e177a068e81781fc` |
| postgres | `15-alpine` `sha256:a8e042598834300dfaa17a54f695b87b7f25d3490433bbbe2ef3bffbb9747f24` |
| redis | `7.2.11-alpine` `sha256:1a34bdba051ecd8a58ec8a3cc460acef697a1605e918149cc53d920673c1a0a7` |
| kvrocks | `2.15.0` `sha256:0192707bd335ad546d8ec7826b1553b674fff27d6cea4195e22c0f1adbdbea40` |
| opa | `1.12.2` `sha256:b1400dba9c2356292d8229cbceddcd95158c7d2a8e1f53947ae3bd8e70478b61` |
| traefik | `v3.6` `sha256:31267173a15b4944e797a76ffd9c419707c8d8b32fe5b610f80cd0cfa05f372d` |
| cvat-sdk | `2.80.0` (`uv.lock`) |
| Patched server | `cvat-labeling/server:v2.80.0-guard1` = upstream image + `cvat/patches/0001-write-guard.patch` (thin overlay, `cvat/Dockerfile.guard`) |

Images are published for `linux/amd64` only; on Apple Silicon they run emulated, so local timings are **not** capacity evidence (see capacity doc).

## Capability matrix (verified by `tests/capability`, all against the running pinned stack)
| Capability | Result |
| --- | --- |
| Video task creation, ingestion polling (`/api/requests`), job split via `segment_size` | works |
| **Default `overlap` is non-zero for video → jobs overlap** (5 frames) | **Integration must send `overlap=0`** (test `test_default_overlap_makes_jobs_overlap`) |
| `start_frame` / `frame_filter=step=N` | works; CVAT frame *i* = clip frame `start_frame + i*step` |
| Per-frame timestamps from CVAT | **absent** (`data/meta.frames` has one entry for video) → integration persists own ffprobe map (ADR 0004 / `framemap.py`) |
| Stage/state pairs from the spec table, assignee, issue create/resolve | works; stage change alone resets state to `new` |
| Annotation export (CVAT for video 1.1) via `/dataset/export` + requests API | works |
| Webhooks: org scope, `update:job`/`update:task`, `ping` | works; `X-Signature-256: sha256=<hmac(raw body)>`; **no delivery-id or event header** |
| Personal access tokens (`Bearer`), read-only PATs | works |
| Org provisioning via REST | org create works; **no add-member endpoint** (invitations require an email backend) |
| Social/OIDC login | **not exposed in Community** — see ADR 0002 |
| Server-side freeze | **not native** — see ADR 0003 |

## Consequences
- Webhook dedupe keys derive from `event` + resource id + `updated_date` (Phase 1).
- Smokescreen blocks private targets; the host-run API is allowlisted via `SMOKESCREEN_OPTS=--allow-address` (`deploy/stack.sh`).
- The compose healthcheck must require the JSON body: Traefik serves the UI (HTTP 200) for `/api` while the server restarts.
