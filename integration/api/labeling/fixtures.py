"""In-memory host adapters for the standalone harness (development/test only, never packaged for the marketplace)."""

from __future__ import annotations

from .adapters import Principal


class FixtureIdentity:
    def __init__(self, users: dict[str, Principal]):
        self._users = users

    def resolve(self, credentials: str, tenant_hint: str | None = None) -> Principal:
        try:
            return self._users[credentials]
        except KeyError:
            raise PermissionError("unknown credentials") from None


    def memberships(self, credentials: str) -> list[Principal]:
        return [self.resolve(credentials)]


class FixtureMedia:
    def __init__(self, assets: dict[tuple[str, str], dict]):
        self._assets = assets

    def get(self, tenant_id: str, source_asset_id: str) -> dict:
        return self._assets[(tenant_id, source_asset_id)]  # KeyError across tenants by construction

    def open_url(self, tenant_id: str, source_asset_id: str, ttl_s: int = 900) -> str:
        self.get(tenant_id, source_asset_id)
        return f"https://fixture.invalid/{tenant_id}/{source_asset_id}?ttl={ttl_s}"

    def list_assets(self, tenant_id: str) -> list[dict]:
        return [v for (t, _), v in sorted(self._assets.items()) if t == tenant_id]


class LocalDirMediaCatalog:
    """Dev/standalone catalog: clips live under `<root>/<tenant_id>/*.mp4`, so a tenant can only ever see its own directory.
    References (sha256 + probed frame map) are built once per file version. The marketplace binds its own catalog instead."""

    def __init__(self, root):
        from pathlib import Path

        self._root = Path(root)
        self._cache: dict[tuple, dict] = {}

    def _dir(self, tenant_id: str):
        if not tenant_id or "/" in tenant_id or ".." in tenant_id:
            raise KeyError(tenant_id)
        return self._root / tenant_id

    def _ref(self, tenant_id: str, path) -> dict:
        key = (tenant_id, path.name, path.stat().st_mtime_ns, path.stat().st_size)
        if key not in self._cache:
            self._cache[key] = make_media_ref(path, path.stem, root=self._root)
        return self._cache[key]

    def get(self, tenant_id: str, source_asset_id: str) -> dict:
        path = self._dir(tenant_id) / f"{source_asset_id}.mp4"
        if "/" in source_asset_id or not path.is_file():
            raise KeyError(source_asset_id)
        return self._ref(tenant_id, path)

    def open_url(self, tenant_id: str, source_asset_id: str, ttl_s: int = 900) -> str:
        self.get(tenant_id, source_asset_id)
        return f"https://fixture.invalid/{tenant_id}/{source_asset_id}?ttl={ttl_s}"

    def list_assets(self, tenant_id: str) -> list[dict]:
        d = self._dir(tenant_id)
        return [self._ref(tenant_id, p) for p in sorted(d.glob("*.mp4"))] if d.is_dir() else []


class FixtureModels:
    def __init__(self, entitlements: dict[str, dict[str, dict]]):
        self._e = entitlements

    def list_models(self, tenant_id: str) -> list[dict]:
        return [{**v, "model_id": k} for k, v in self._e.get(tenant_id, {}).items()]

    def entitled(self, tenant_id: str, model_id: str) -> bool:
        return model_id in self._e.get(tenant_id, {})

    def resolve(self, tenant_id: str, model_id: str) -> dict:
        return self._e[tenant_id][model_id]


class FixturePublisher:
    """Idempotent on dataset_version_id. `fail_next` injects transient catalog outages."""

    def __init__(self):
        self.acks: dict[str, str] = {}
        self.calls = 0
        self.fail_next = 0

    def register(self, tenant_id, dataset_version_id, manifest_uri, manifest_sha256) -> str:
        self.calls += 1
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("catalog unavailable")
        return self.acks.setdefault(dataset_version_id, f"ack-{dataset_version_id}")


class LocalObjectStore:
    """Write-once local store (dev/test): O_EXCL create, read-only files, refuses overwrite with different bytes."""

    def __init__(self, root):
        from pathlib import Path
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str):
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise PermissionError("key escapes store root")
        return p

    def put_if_absent(self, key: str, data: bytes) -> tuple[str, str]:
        import hashlib
        import os

        from .adapters import ImmutableObjectError

        p = self._path(key)
        sha = hashlib.sha256(data).hexdigest()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        except FileExistsError:
            if hashlib.sha256(p.read_bytes()).hexdigest() != sha:
                raise ImmutableObjectError(f"{key} exists with different content") from None
            return f"file://{p}", sha
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return f"file://{p}", sha

    def exists(self, key: str):
        import hashlib
        p = self._path(key)
        return (f"file://{p}", hashlib.sha256(p.read_bytes()).hexdigest()) if p.exists() else None

    def get(self, uri: str) -> bytes:
        from pathlib import Path
        p = Path(uri.removeprefix("file://")).resolve()
        if self.root.resolve() not in p.parents:
            raise PermissionError("uri outside store")
        return p.read_bytes()


class RecordingSink:
    def __init__(self):
        self.sent: list[tuple] = []

    def notify(self, tenant_id, user_id, kind, payload) -> None:
        self.sent.append((tenant_id, user_id, kind, payload))


class LocalClipSource:
    """Resolves MediaReference.storage.key relative to a local directory (dev/test only; S3 binding comes later)."""

    def __init__(self, root):
        from pathlib import Path
        self.root = Path(root)

    def local_path(self, media_ref: dict):
        p = (self.root / media_ref["storage"]["key"]).resolve()
        if self.root.resolve() not in p.parents:
            raise PermissionError("path escapes clip root")
        return p


def make_media_ref(path, asset_id: str, *, root, clip_start_offset_s: float = 0.0, width: int = 320, height: int = 240) -> dict:
    """Build a schema-valid MediaReference for a local fixture clip (sha256 of file, probed frame map)."""
    import hashlib
    from pathlib import Path

    from .framemap import build_frame_map

    path, root = Path(path), Path(root)
    fm = build_frame_map(path, clip_start_offset_s=clip_start_offset_s)
    return {"source_asset_id": asset_id, "storage": {"bucket": "fixtures", "key": str(path.resolve().relative_to(root.resolve())), "version_id": None},
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "clip": {"start_offset_s": clip_start_offset_s, "duration_s": fm.pts_s[-1] - fm.pts_s[0] + 1 / 30},
            "dimensions": {"width": width, "height": height}, "frame_map": fm.to_json(),
            "telemetry": {"status": "missing", "reason": "no SRT in fixture", "srt_sha256": None}}
