from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    cvat_url: str = field(default_factory=lambda: os.environ.get("CVAT_URL", "http://localhost:8080"))
    cvat_instance: str = field(default_factory=lambda: os.environ.get("CVAT_INSTANCE", "primary"))
    cvat_service_token: str = field(default_factory=lambda: os.environ.get("CVAT_SERVICE_TOKEN", ""))
    # URL CVAT's webhook worker can reach (host-run API from inside Docker Desktop).
    webhook_public_url: str = field(default_factory=lambda: os.environ.get(
        "WEBHOOK_PUBLIC_URL", "http://host.docker.internal:8000/api/v1/labeling/webhooks/cvat"))
    webhook_master_secret: str = field(default_factory=lambda: os.environ.get("LABELING_WEBHOOK_MASTER_SECRET", "dev-only-master-secret"))
    reconcile_interval_s: int = field(default_factory=lambda: int(os.environ.get("LABELING_RECONCILE_INTERVAL_S", "300")))
    segment_size: int = field(default_factory=lambda: int(os.environ.get("LABELING_SEGMENT_SIZE", "300")))

    def tenant_webhook_secret(self, tenant_id: str) -> str:
        """Per-tenant webhook secret derived from the master secret (never stored). CVAT caps secrets at 64 chars."""
        return hmac.new(self.webhook_master_secret.encode(), f"cvat-webhook:{tenant_id}".encode(), hashlib.sha256).hexdigest()


def org_slug_for(tenant_id: str) -> str:
    """CVAT org slugs are <=16 chars; derive a stable, collision-resistant slug from the tenant id."""
    return "lb-" + hashlib.sha256(tenant_id.encode()).hexdigest()[:12]
