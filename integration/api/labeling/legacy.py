"""Optional, explicit bridge to the marketplace's existing correction manifests (ground_truth_service).

Legacy layout: ground-truth/{video_hash}/{model_type}/{model_type}_groundtruth_manifest_{YYYYmmdd_HHMMSS_ffffff}.json
Rules: new dataset versions live under their own `labeling/` prefix and NEVER replace legacy manifests. Writing a
legacy-shaped manifest is opt-in per batch and always creates a new timestamped file (no overwrite, no delete).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

LEGACY_KEY_RE = re.compile(r"^ground-truth/[^/]+/[a-z_]+/[a-z_]+_groundtruth_manifest_\d{8}_\d{6}(_\d+)?\.json$")
VERSION_KEY_PREFIX = "labeling"


def dataset_version_prefix(tenant_id: str, batch_id: str, dataset_version_id: str) -> str:
    return f"{VERSION_KEY_PREFIX}/{tenant_id}/{batch_id}/{dataset_version_id}/"


def legacy_manifest_key(video_hash: str, model_type: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"ground-truth/{video_hash}/{model_type}/{model_type}_groundtruth_manifest_{now.strftime('%Y%m%d_%H%M%S_%f')}.json"


def to_legacy_manifest(*, video_name: str, video_hash: str, model_type: str, run_id: str, saved_by: str,
                       successes: list, failures: list, source_manifest_url: str, source_output_prefix: str,
                       now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    return {
        "videoName": video_name, "failures": failures, "successes": successes,
        "source_manifest_url": source_manifest_url, "source_output_prefix": source_output_prefix,
        "saved_by": saved_by, "saved_at": now.isoformat(), "run_id": run_id,
        "model_type": model_type, "video_hash": video_hash,
    }
