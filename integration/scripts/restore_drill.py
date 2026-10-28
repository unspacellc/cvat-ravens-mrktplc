"""Backup/restore drill against the dev stack. DESTRUCTIVE to dev state by design (restores over it).
Usage: CONFIRM_RESTORE=yes uv run python scripts/restore_drill.py   -> JSON evidence on stdout."""
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from sqlalchemy import select

from api.labeling import fixtures as fx
from api.labeling import operations, provisioning, service
from api.labeling.config import Settings
from api.labeling.cvat_client import CvatClient
from api.labeling.db import make_engine, make_session_factory
from api.labeling.models import LabelingJob, LabelingTask, TenantCvatOrg

if os.environ.get("CONFIRM_RESTORE") != "yes":
    sys.exit("refusing: set CONFIRM_RESTORE=yes (restores over the dev stack)")
FIX = ROOT / "fixtures" / "generated"
tok = httpx.post("http://localhost:8080/api/auth/login", json={"username": "admin", "password": "Adm1n-local-only!"}).json()["key"]
ctx = service.Ctx(Settings(cvat_service_token=tok), CvatClient("http://localhost:8080", tok), None, fx.LocalClipSource(FIX))
sf = make_session_factory(make_engine())
tenant = f"drill-{uuid.uuid4().hex[:6]}"
ref = fx.make_media_ref(FIX / "p1_c.mp4", f"asset-{tenant}", root=FIX)
ctx.media = fx.FixtureMedia({(tenant, ref["source_asset_id"]): ref})
sh = lambda *a: subprocess.run(a, check=True, capture_output=True, text=True).stdout.strip()

with sf() as s:
    schema = service.register_schema(s, tenant, "1.0.0", [{"name": "car", "type": "rectangle", "attributes": []}])
    _, op = service.create_batch(s, ctx, tenant_id=tenant, name="drill", asset_ids=[ref["source_asset_id"]], schema_version_id=schema.id, idempotency_key="drill", segment_size=40)
    s.commit(); op_id = op.id
while operations.run_one(sf, {"provision_batch": provisioning.provision_batch(ctx)}, op_id=op_id):
    pass

def snapshot():
    with sf() as s:
        org = s.get(TenantCvatOrg, (tenant, "primary"))
        jobs = s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == tenant).order_by(LabelingJob.cvat_job_id)).all()
        task = s.scalars(select(LabelingTask).where(LabelingTask.tenant_id == tenant)).one()
        h = {"Authorization": f"Token {tok}", "X-Organization": org.cvat_org_slug}
        frame = httpx.get(f"http://localhost:8080/api/jobs/{jobs[0].cvat_job_id}/data", params={"type": "frame", "number": 0, "quality": "original"}, headers=h, timeout=60)
        ann = httpx.get(f"http://localhost:8080/api/jobs/{jobs[0].cvat_job_id}/annotations", headers=h, timeout=60).json()
        remote = [ctx.cvat.get_job(org.cvat_org_slug, j.cvat_job_id) for j in jobs]
        return {"portal_jobs": [(j.cvat_job_id, j.start_frame, j.stop_frame, j.cvat_state, j.annotation_revision) for j in jobs],
                "cvat_job_states": [(r["id"], r["stage"], r["state"]) for r in remote], "frame0_sha256": hashlib.sha256(frame.content).hexdigest(),
                "shapes_job0": len(ann["shapes"]), "cvat_task_id": task.cvat_task_id, "slug": org.cvat_org_slug}

# baseline: a state change + an annotation
with sf() as s:
    org = s.get(TenantCvatOrg, (tenant, "primary")); job0 = s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == tenant).order_by(LabelingJob.cvat_job_id)).first()
    slug, jid = org.cvat_org_slug, job0.cvat_job_id
ctx.cvat._req("PATCH", f"/api/jobs/{jid}", org=slug, json={"state": "in progress"})
lab = ctx.cvat._req("GET", "/api/labels", org=slug, params={"job_id": jid}).json()["results"][0]["id"]
ctx.cvat._req("PATCH", f"/api/jobs/{jid}/annotations", org=slug, params={"action": "create"}, json={"shapes": [{"type": "rectangle", "frame": 0, "label_id": lab, "points": [1, 1, 9, 9], "attributes": [], "occluded": False, "outside": False, "z_order": 0, "source": "manual"}], "tags": [], "tracks": []})
from api.labeling import reconcile

reconcile.reconcile_tenant(sf, ctx, tenant)
s0 = snapshot()

t_backup0 = time.time(); bdir = sh(str(ROOT / "deploy/backup.sh"), str(ROOT / "backups")).splitlines()[-1]; backup_s = time.time() - t_backup0
t_damage = time.time()
# damage: lose the CVAT task + state change + extra annotation, and wipe the integration rows
ctx.cvat._req("DELETE", f"/api/tasks/{s0['cvat_task_id']}", org=slug, ok=(204,))
with sf() as s:
    for j in s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == tenant)):
        s.delete(j)
    s.commit()
rpo_s = t_damage - t_backup0

t_restore0 = time.time()
subprocess.run([str(ROOT / "deploy/restore.sh"), bdir], check=True, capture_output=True, text=True, env={**os.environ, "CONFIRM_RESTORE": "yes"})
deadline = time.time() + 300
while time.time() < deadline:
    try:
        if "version" in httpx.get("http://localhost:8080/api/server/about", timeout=5).text:
            break
    except httpx.HTTPError:
        pass
    time.sleep(2)
time.sleep(5)
rto_s = time.time() - t_restore0
sf = make_session_factory(make_engine())  # fresh pool after the DB was recreated
s1 = snapshot()
reconcile_stats = reconcile.reconcile_tenant(sf, ctx, tenant)
ok = {k: s0[k] == s1[k] for k in s0}
print(json.dumps({"backup_dir": bdir, "backup_seconds": round(backup_s, 1), "restore_seconds_to_api": round(rto_s, 1),
                  "achieved_rpo_window_s": round(rpo_s, 1), "matches_baseline": ok, "reconcile_after_restore": reconcile_stats,
                  "all_match": all(ok.values()) and reconcile_stats["missing"] == 0 and reconcile_stats["errors"] == 0}, indent=2))
