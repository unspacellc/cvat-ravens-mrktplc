"""Dev seed for the standalone host: two tenants, members whose IDs are the IdP `sub` you type at the mock IdP login,
and one provisioned batch each. Idempotent per run name. Usage: uv run python scripts/seed_dev.py"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.labeling import fixtures as fx  # noqa: E402
from api.labeling import operations, provisioning, service  # noqa: E402
from api.labeling.config import Settings  # noqa: E402
from api.labeling.cvat_client import CvatClient  # noqa: E402
from api.labeling.db import make_engine, make_session_factory  # noqa: E402
from api.labeling.models import LabelingMember  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "generated"
CVAT = os.environ.get("CVAT_URL", "http://localhost:8080")
PEOPLE = {
    "acme": [("acme-admin", ["admin"]), ("acme-lab1", ["labeler"]), ("acme-lab2", ["labeler"]), ("acme-rev", ["reviewer"])],
    "globex": [("globex-admin", ["admin"]), ("globex-lab", ["labeler"])],
    "both": [("multi", ["admin"])],  # member of both tenants, to try tenant switching
}


def main() -> None:
    token = httpx.post(f"{CVAT}/api/auth/login", json={"username": "admin", "password": "Adm1n-local-only!"}).json()["key"]
    clip = FIXTURES / "seed_clip.mp4"
    FIXTURES.mkdir(parents=True, exist_ok=True)
    if not clip.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=4", "-pix_fmt", "yuv420p", str(clip)], check=True)
    sf = make_session_factory(make_engine())
    tenants = {"acme": "tenant-acme", "globex": "tenant-globex"}
    media = {(t, f"{t}-clip"): fx.make_media_ref(clip, f"{t}-clip", root=FIXTURES) for t in tenants.values()}
    ctx = service.Ctx(settings=Settings(cvat_service_token=token, segment_size=40), cvat=CvatClient(CVAT, token), media=fx.FixtureMedia(media), clips=fx.LocalClipSource(FIXTURES))
    with sf() as s:
        for key, t in tenants.items():
            for sub, roles in PEOPLE[key] + PEOPLE["both"]:
                s.merge(LabelingMember(tenant_id=t, user_id=sub, roles=roles, status="active", email=f"{sub}@example.com"))
            schema = service.register_schema(s, t, "1.0.0", [{"name": "car", "type": "rectangle", "attributes": []}])
            _, op = service.create_batch(s, ctx, tenant_id=t, name=f"{key.title()} crossing footage", asset_ids=[f"{t}-clip"],
                                         schema_version_id=schema.id, idempotency_key=hashlib.sha256(t.encode()).hexdigest()[:16])
            s.commit()
            while operations.run_one(sf, {"provision_batch": provisioning.provision_batch(ctx)}, op_id=op.id):
                pass
    print("seeded. Sign in at the mock IdP with username = one of:", ", ".join(sub for ps in PEOPLE.values() for sub, _ in ps))


if __name__ == "__main__":
    main()
