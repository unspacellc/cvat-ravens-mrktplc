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
    # Browser-facing CVAT origin and the OIDC provider id CVAT exposes (editor launch targets this only).
    cvat_public_url: str = field(default_factory=lambda: os.environ.get("CVAT_PUBLIC_URL", os.environ.get("CVAT_URL", "http://localhost:8080")))
    oidc_provider_id: str = field(default_factory=lambda: os.environ.get("LABELING_OIDC_PROVIDER_ID", "portal"))
    cvat_server_version: str = field(default_factory=lambda: os.environ.get("CVAT_SERVER_VERSION", "2.80.0"))
    cvat_image_digest: str = field(default_factory=lambda: os.environ.get(
        "CVAT_IMAGE_DIGEST", "sha256:955ae3b34089fbd57d22bf9a692cef82c99647f9970dd68a716448eec252f425"))
    reconcile_interval_s: int = field(default_factory=lambda: int(os.environ.get("LABELING_RECONCILE_INTERVAL_S", "300")))
    segment_size: int = field(default_factory=lambda: int(os.environ.get("LABELING_SEGMENT_SIZE", "300")))

    def tenant_webhook_secret(self, tenant_id: str) -> str:
        """Per-tenant webhook secret derived from the master secret (never stored). CVAT caps secrets at 64 chars."""
        return hmac.new(self.webhook_master_secret.encode(), f"cvat-webhook:{tenant_id}".encode(), hashlib.sha256).hexdigest()


def org_slug_for(tenant_id: str) -> str:
    """CVAT org slugs are <=16 chars; derive a stable, collision-resistant slug from the tenant id."""
    return "lb-" + hashlib.sha256(tenant_id.encode()).hexdigest()[:12]


def cvat_username_for(user_id: str) -> str:
    """Stable CVAT username derived from the IdP `sub`, so CVAT identity never depends on IdP display claims."""
    return "lb-u-" + hashlib.sha256(user_id.encode()).hexdigest()[:20]


def cvat_role_for(roles) -> str:
    """Least privilege. In CVAT v2.80.0 only maintainers see jobs they are not assigned to (supervisor == worker for
    job visibility), so reviewers get worker access to the jobs assigned to them for review; only tenant admins are maintainers."""
    return "maintainer" if "admin" in roles else "worker"
