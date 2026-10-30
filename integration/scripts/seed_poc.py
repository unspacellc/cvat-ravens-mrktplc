"""Proof-of-concept seed: puts the real test user (signed in through the marketplace API) into their own organization's
tenant as admin+labeler and provisions one batch on the local CVAT. Idempotent per run.
Needs LABELING_TEST_EMAIL / LABELING_TEST_PASSWORD (deploy/.env) and the local stack. Usage:
  set -a; . deploy/.env; set +a; uv run python scripts/seed_poc.py"""
import base64
import hashlib
import json
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

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "generated"
CVAT = os.environ.get("CVAT_URL", "http://localhost:8080")
MARKETPLACE = os.environ.get("MARKETPLACE_API_URL", "https://api.unspace.cassyork.com") + "/api/v1"


def marketplace_identity() -> tuple[str, str, str]:
    """(tenant id = the user's marketplace organization, Cognito sub, email), from a real sign-in."""
    login = httpx.post(f"{MARKETPLACE}/auth/login", json={"email": os.environ["LABELING_TEST_EMAIL"], "password": os.environ["LABELING_TEST_PASSWORD"]})
    login.raise_for_status()
    token = login.json()["tokens"]["accessToken"]
    me = httpx.get(f"{MARKETPLACE}/auth/me", headers={"authorization": f"Bearer {token}"}).json()
    sub = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))["sub"]
    return me["customer"]["id"], sub, me["email"]


def main() -> None:
    tenant, sub, email = marketplace_identity()
    token = httpx.post(f"{CVAT}/api/auth/login", json={"username": "admin", "password": "Adm1n-local-only!"}).json()["key"]
    # Clips live under <clip root>/<tenant id>/, which is also what the portal API's media catalog lists for that tenant.
    tenant_dir = FIXTURES / tenant
    tenant_dir.mkdir(parents=True, exist_ok=True)
    for name, pattern, secs in (("crossing-north", "testsrc2", 4), ("crossing-south", "smptebars", 3), ("yard-entry", "testsrc", 2)):
        clip = tenant_dir / f"{name}.mp4"
        if not clip.exists():
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"{pattern}=size=320x240:rate=30:duration={secs}", "-pix_fmt", "yuv420p", str(clip)], check=True)
    ctx = service.Ctx(settings=Settings(cvat_service_token=token, segment_size=40), cvat=CvatClient(CVAT, token),
                      media=fx.LocalDirMediaCatalog(FIXTURES), clips=fx.LocalClipSource(FIXTURES))
    sf = make_session_factory(make_engine())
    with sf() as s:
        service.set_member(s, ctx, tenant_id=tenant, user_id=sub, roles=["admin", "labeler"], email=email)
        schema = service.register_schema(s, tenant, "1.0.0", [{"name": "car", "type": "rectangle", "attributes": []}])
        _, op = service.create_batch(s, ctx, tenant_id=tenant, name="Unspace proof-of-concept footage", asset_ids=["crossing-north"],
                                     schema_version_id=schema.id, idempotency_key=hashlib.sha256(f"poc-{tenant}-crossing-north".encode()).hexdigest()[:16])
        s.commit()
        while operations.run_one(sf, {"provision_batch": provisioning.provision_batch(ctx)}, op_id=op.id):
            pass
    print(f"seeded tenant {tenant} (Unspace) with member {sub[:8]}… roles admin+labeler")


if __name__ == "__main__":
    main()
