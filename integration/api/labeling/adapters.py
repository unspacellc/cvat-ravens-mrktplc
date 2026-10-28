"""Host adapter contracts. The marketplace binds these to its real identity/media/model/catalog services at
incorporation; `fixtures.py` provides in-memory implementations for the standalone build and contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    roles: frozenset[str]  # subset of {"labeler","reviewer","admin","creator"}
    email: str | None = None


@runtime_checkable
class IdentityContext(Protocol):
    def resolve(self, credentials: str, tenant_hint: str | None = None) -> Principal:
        """Server-side tenant membership resolution. `tenant_hint` only selects among memberships the user already
        holds. Raises PermissionError when unauthenticated/unknown/not a member."""

    def memberships(self, credentials: str) -> list[Principal]:
        """All tenants the authenticated user may act in (empty list is valid), for tenant selection."""


@runtime_checkable
class MediaCatalog(Protocol):
    def get(self, tenant_id: str, source_asset_id: str) -> dict:
        """A MediaReference-shaped dict (immutable content reference + sha256). KeyError if absent in this tenant."""

    def open_url(self, tenant_id: str, source_asset_id: str, ttl_s: int = 900) -> str:
        """Short-lived transport URL. Never persisted, never a stable identifier."""


@runtime_checkable
class ModelCatalog(Protocol):
    def entitled(self, tenant_id: str, model_id: str) -> bool: ...

    def resolve(self, tenant_id: str, model_id: str) -> dict:
        """Immutable artifact identity: artifact URI/digest, class mapping, preprocessing, settings."""


@runtime_checkable
class DatasetPublisher(Protocol):
    def register(self, tenant_id: str, dataset_version_id: str, manifest_uri: str, manifest_sha256: str) -> str:
        """Idempotent on dataset_version_id; returns a catalog ack id. Never creates a second version on replay."""


@runtime_checkable
class NotificationSink(Protocol):
    def notify(self, tenant_id: str, user_id: str, kind: str, payload: dict) -> None:
        """Best-effort; failure must never block saving work."""


@runtime_checkable
class ClipSource(Protocol):
    def local_path(self, media_ref: dict):
        """Materialize the clip described by a MediaReference for CVAT upload and frame-map probing (returns Path)."""
