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
from sqlalchemy import func, select, text

from api.labeling import fixtures as fx
from api.labeling import operations, provisioning, review, sendoff, service
from api.labeling.adapters import Principal
from api.labeling.config import Settings
from api.labeling.cvat_client import CvatClient
from api.labeling.db import make_engine, make_session_factory
from api.labeling.models import (
    AnnotationSnapshot,
    DatasetVersion,
    LabelingBatch,
    LabelingJob,
    LabelingMember,
    LabelingTask,
    Operation,
    Outbox,
    ReviewEvent,
    TenantCvatOrg,
)

if os.environ.get("CONFIRM_RESTORE") != "yes":
    sys.exit("refusing: set CONFIRM_RESTORE=yes (restores over the dev stack)")
FIX = ROOT / "fixtures" / "generated"
tok = httpx.post("http://localhost:8080/api/auth/login", json={"username": "admin", "password": "Adm1n-local-only!"}).json()["key"]
STORE = ROOT / "var" / "objects"  # object store lives outside the DB/volumes the backup covers (S3 with versioning in production)
ctx = service.Ctx(Settings(cvat_service_token=tok), CvatClient("http://localhost:8080", tok), None, fx.LocalClipSource(FIX),
                  store=fx.LocalObjectStore(STORE), publisher=fx.FixturePublisher())
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

# deliver one batch end to end so the backup holds review snapshots, a dataset version and its outbox event
lab, rev = f"lab-{tenant}", f"rev-{tenant}"
with sf() as s:
    s.add(LabelingMember(tenant_id=tenant, user_id=lab, roles=["labeler"], email=f"{lab}@example.com"))
    s.add(LabelingMember(tenant_id=tenant, user_id=rev, roles=["reviewer"], email=f"{rev}@example.com"))
    s.commit()
    job_ids = [j.id for j in s.scalars(select(LabelingJob).where(LabelingJob.tenant_id == tenant).order_by(LabelingJob.start_frame))]
    batch_id = s.scalars(select(LabelingBatch.id).where(LabelingBatch.tenant_id == tenant)).one()
    task_cvat = s.scalars(select(LabelingTask.cvat_task_id).where(LabelingTask.tenant_id == tenant)).one()
labels = ctx.cvat.task_labels(slug, task_cvat)
for i, pjid in enumerate(job_ids):
    with sf() as s:
        service.assign_job(s, ctx, tenant_id=tenant, job_id=pjid, user_id=lab, username=service.cvat_username_for(lab), uid=lab)
        s.commit()
        j = s.get(LabelingJob, pjid)
        ctx.cvat.put_job_annotations(slug, j.cvat_job_id, {"version": 0, "tags": [], "tracks": [], "shapes": [
            {"type": "rectangle", "frame": j.start_frame, "label_id": next(iter(labels)), "points": [5 + i, 5, 60 + i, 60], "occluded": False,
             "attributes": [], "source": "manual", "z_order": 0, "rotation": 0, "outside": False}]})
        review.transition(s, ctx, actor=Principal(user_id=lab, tenant_id=tenant, roles=frozenset({"labeler"})), job_id=pjid, cmd=review.Command("submit", j.revision, reviewer_id=rev))
        s.commit()
        sha = review.live_review_sha(ctx, s, tenant, pjid)
    with sf() as s:
        j = s.get(LabelingJob, pjid)
        review.transition(s, ctx, actor=Principal(user_id=rev, tenant_id=tenant, roles=frozenset({"reviewer"})), job_id=pjid, cmd=review.Command("approve", j.revision, attestation="drill", expected_snapshot_sha256=sha))
        s.commit()
with sf() as s:
    dv, sop = sendoff.request_send_off(s, ctx, tenant_id=tenant, batch_id=batch_id, idempotency_key="drill-so")
    s.commit()
    dv_id, sop_id = dv.id, sop.id
while operations.run_one(sf, {"send_off": sendoff.send_off(ctx)}, op_id=sop_id):
    pass

TABLES = {"batch": LabelingBatch, "task": LabelingTask, "job": LabelingJob, "snapshot": AnnotationSnapshot, "review_event": ReviewEvent,
          "dataset_version": DatasetVersion, "outbox": Outbox, "operation": Operation}


def integration_state():
    """What the integration DB must still hold after a restore: row counts per table for this tenant, the migration revision,
    and the published version with proof that the object its manifest points to is intact."""
    with sf() as s:
        counts = {n: s.scalar(select(func.count()).select_from(m).where(m.tenant_id == tenant)) for n, m in TABLES.items()}
        everything = {t: s.scalar(text(f'select count(*) from "{t}"')) for (t,) in s.execute(
            text("select tablename from pg_tables where schemaname = 'public' order by 1")).all()}
        d = s.get(DatasetVersion, dv_id)
        manifest = ctx.store.get(d.manifest_uri) if d.manifest_uri else b""
        return {"row_counts": counts, "all_tables_row_counts": everything, "alembic_revision": s.scalar(text("select version_num from alembic_version")),
                "dataset_version": {"status": d.status, "manifest_sha256": d.manifest_sha256, "jobs_in_revision_set": len(d.revision_set)},
                "manifest_object_matches_recorded_sha256": hashlib.sha256(manifest).hexdigest() == d.manifest_sha256}


def barrier_holds():
    try:
        ctx.cvat._req("PATCH", f"/api/jobs/{jid}", org=slug, json={"state": "new"})
        return False
    except Exception as e:  # noqa: BLE001
        return "423" in str(e)


s0 = {**snapshot(), **integration_state(), "write_barrier_holds": barrier_holds()}
pre = {"published": s0["dataset_version"]["status"] == "published", "manifest_ok": s0["manifest_object_matches_recorded_sha256"], "barrier": s0["write_barrier_holds"]}
assert all(pre.values()), f"drill precondition failed before any damage: {pre}"

t_backup0 = time.time(); bdir = sh(str(ROOT / "deploy/backup.sh"), str(ROOT / "backups")).splitlines()[-1]; backup_s = time.time() - t_backup0
t_damage = time.time()
# damage: lose the CVAT task + state change + extra annotation, and wipe the integration rows
ctx.cvat.unfreeze_task(s0["cvat_task_id"])  # the guard blocks deletes; a real disaster would not ask
ctx.cvat._req("DELETE", f"/api/tasks/{s0['cvat_task_id']}", org=slug, ok=(204,))
with sf() as s:  # total loss of the integration DB's content (row triggers keep reviews append-only, so TRUNCATE, not DELETE)
    names = [r[0] for r in s.execute(text("select tablename from pg_tables where schemaname = 'public' and tablename <> 'alembic_version'"))]
    s.execute(text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " CASCADE"))
    s.commit()
    assert s.scalar(select(func.count()).select_from(LabelingJob)) == 0
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
s1 = {**snapshot(), **integration_state(), "write_barrier_holds": barrier_holds()}
reconcile_stats = reconcile.reconcile_tenant(sf, ctx, tenant)
ok = {k: s0[k] == s1[k] for k in s0}
print(json.dumps({"backup_dir": bdir, "backup_seconds": round(backup_s, 1), "restore_seconds_to_api": round(rto_s, 1),
                  "achieved_rpo_window_s": round(rpo_s, 1), "matches_baseline": ok, "reconcile_after_restore": reconcile_stats,
                  "all_match": all(ok.values()) and reconcile_stats["missing"] == 0 and reconcile_stats["errors"] == 0}, indent=2))
