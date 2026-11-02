"""Object-store key layout. Every key starts with the organization (tenant) id, matching the marketplace's per-organization
siloing (`{org-uuid}/...`), then a `labeling/` namespace so nothing here can collide with yard media or the legacy
global `ground-truth/` prefix."""

from __future__ import annotations


def tenant_key(tenant_id: str, *parts: str) -> str:
    if not tenant_id or "/" in tenant_id or ".." in tenant_id:
        raise ValueError("invalid tenant id for an object key")
    return "/".join([tenant_id, "labeling", *(p.strip("/") for p in parts)])


def dataset_prefix(tenant_id: str, batch_id: str, dataset_version_id: str) -> str:
    return tenant_key(tenant_id, "datasets", batch_id, dataset_version_id)


def snapshot_key(tenant_id: str, job_id: str, sha256: str) -> str:
    return tenant_key(tenant_id, "snapshots", job_id, f"{sha256}.json")


def prelabel_key(tenant_id: str, run_id: str, *parts: str) -> str:
    return tenant_key(tenant_id, "prelabel", run_id, *parts)
