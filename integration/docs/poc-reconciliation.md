# Companion POC reconciliation

Source: "Annotation & Trim POC Plan — CVAT + Shotstack on AWS" (Oct 23, 2026), received 2026-10-28. Diff against this build:

| POC element | Status in this build | Action |
| --- | --- | --- |
| One CVAT org per customer org, org-scoped storage, PAT service account | Matches (org per tenant, webhook per tenant) | Per-org S3 cloud storage + scoped IAM key is Phase 2; superuser token caveat (phase-1.md) stands |
| Auth bridge logging into CVAT with a random stored password | **Replaced** by real OIDC login (ADR 0002/0005): no per-user CVAT passwords to store | Keep bridge out of scope; drop POC "random password in Secrets Manager" |
| Trim worker (FFmpeg) writes clip + retimed `.SRT` + `.json` + `manifest.jsonl` under `<org>/clips/<stem>/` | Not built (upstream of this scope) | Our `MediaRef`/`FrameMap` must take the trim worker's `actual_in_s` and `srt_offset_s`; add as inputs in Phase 2 contracts |
| Frame alignment: SRT cue 1 = clip frame 0 = CVAT frame 0 | Consistent with `FrameMap` (`clip_frame = start_frame + cvat_frame*step`); trimmed clips start at start_frame 0 | Add a contract test pairing SRT cue index to frame index (Phase 2) |
| Re-encode for training clips (frame-accurate) | Good: avoids keyframe-snap drift in our frame map | Reject stream-copy clips whose real start offset is not recorded |
| CVAT S3 cloud storage + `manifest.jsonl` task creation | Phase 1 uses local fixtures + upload | Switch ingestion to cloud storage in Phase 2 |
| Webhook on job completion -> export to `<org>/datasets/<id>/` | Matches Phase 3 dataset publish; webhook intake exists | Export target + registry write-once rule go into the dataset contract |
| Dataset registry (versions, lineage, grants, write-once) | Our `dataset_version` schema covers version/lineage; **grants and Object Lock are not in contracts** | Decide whether sharing grants belong to this module or the marketplace |
| Teams inside an org (lead/labeler) with CVAT sync | Not built. CVAT has no teams; assignment is per job | Add team->assignment mapping to Phase 2 scope |
| Training via SageMaker, model artifacts registered in marketplace | Out of scope here | Registration source = SageMaker (ADR 0004) |
| Shotstack SDK as trim UI only (PolyForm Shield) | Out of scope; licensing is the POC owner's call | None |
| Test personas (2 orgs, 5 users) and isolation checks incl. S3 key denial | Our tenant tests cover CVAT; S3 denial needs a real AWS account | Reuse personas in Phase 2 isolation suite |
| CVAT Enterprise SSO | Not purchased (owner) | Cognito via Community OIDC patch |
| EC2/ALB/private subnets/cost guardrails | Phase 6 deployment | Use POC cost guardrails as the starting checklist |
