import uuid

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from api.labeling import models as m

from .conftest import alembic_cfg


def _schema(session, tenant, ver="1.0.0"):
    s = m.LabelSchemaVersion(tenant_id=tenant, semver=ver, content={"labels": [{"name": "car"}]}, content_hash=uuid.uuid4().hex * 2)
    session.add(s); session.flush()
    return s


def _project(session, tenant, schema_tenant=None, schema_id=None):
    s = _schema(session, tenant) if schema_id is None else None
    p = m.LabelingProject(tenant_id=tenant, schema_version_id=schema_id or s.id, cvat_instance="i1")
    session.add(p); session.flush()
    return p


def test_migration_roundtrip(db_url):
    cfg = alembic_cfg(db_url)
    command.downgrade(cfg, "base")
    assert "labeling_job" not in inspect(create_engine(db_url)).get_table_names()
    command.upgrade(cfg, "head")
    assert {"labeling_job", "operation", "webhook_inbox", "outbox_events", "tenant_cvat_org"} <= set(inspect(create_engine(db_url)).get_table_names())


def test_cross_tenant_schema_reference_rejected(session):
    s_a = _schema(session, "tenant-a")
    with pytest.raises(IntegrityError):
        session.add(m.LabelingProject(tenant_id="tenant-b", schema_version_id=s_a.id, cvat_instance="i1"))
        session.flush()


def test_cross_tenant_chain_rejected_at_every_level(session):
    pa = _project(session, "tenant-a")
    batch = m.LabelingBatch(tenant_id="tenant-a", project_id=pa.id, name="b", media_manifest=[])
    session.add(batch); session.flush()
    for bad in (
        m.LabelingBatch(tenant_id="tenant-b", project_id=pa.id, name="x", media_manifest=[]),
        m.LabelingTask(tenant_id="tenant-b", batch_id=batch.id, source_asset_id="a", clip={}, cvat_instance="i1", segment_size=10),
    ):
        with pytest.raises(IntegrityError):
            session.add(bad); session.flush()
        session.rollback()


def test_job_requires_same_tenant_task_and_unique_cvat_ref(session):
    pa = _project(session, "tenant-a")
    b = m.LabelingBatch(tenant_id="tenant-a", project_id=pa.id, name="b", media_manifest=[]); session.add(b); session.flush()
    t = m.LabelingTask(tenant_id="tenant-a", batch_id=b.id, source_asset_id="a", clip={}, cvat_instance="i1", cvat_task_id=7, segment_size=10)
    session.add(t); session.flush()
    session.add(m.LabelingJob(tenant_id="tenant-a", task_id=t.id, cvat_instance="i1", cvat_job_id=70, start_frame=0, stop_frame=9)); session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():  # cross-tenant task reference
        session.add(m.LabelingJob(tenant_id="tenant-b", task_id=t.id, cvat_instance="i1", cvat_job_id=71, start_frame=0, stop_frame=9)); session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():  # same CVAT job on same instance
        session.add(m.LabelingJob(tenant_id="tenant-a", task_id=t.id, cvat_instance="i1", cvat_job_id=70, start_frame=0, stop_frame=9)); session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():  # same CVAT task id on same instance = duplicate mapping
        session.add(m.LabelingTask(tenant_id="tenant-a", batch_id=b.id, source_asset_id="z", clip={}, cvat_instance="i1", cvat_task_id=7, segment_size=10)); session.flush()
    # same CVAT task id on a *different* instance is fine (ids are instance-scoped)
    session.add(m.LabelingTask(tenant_id="tenant-a", batch_id=b.id, source_asset_id="y", clip={}, cvat_instance="i2", cvat_task_id=7, segment_size=10)); session.flush()


def test_operation_idempotency_key_is_unique_per_tenant_and_kind(session):
    session.add(m.Operation(tenant_id="t1", kind="provision_batch", idempotency_key="k1", request_hash="h")); session.flush()
    session.add(m.Operation(tenant_id="t2", kind="provision_batch", idempotency_key="k1", request_hash="h")); session.flush()  # other tenant ok
    with pytest.raises(IntegrityError):
        session.add(m.Operation(tenant_id="t1", kind="provision_batch", idempotency_key="k1", request_hash="h2")); session.flush()


def test_webhook_inbox_dedupes_per_instance(session):
    session.add(m.WebhookInbox(cvat_instance="i1", dedupe_key="k", event="update:job", payload={})); session.flush()
    with pytest.raises(IntegrityError):
        session.add(m.WebhookInbox(cvat_instance="i1", dedupe_key="k", event="update:job", payload={})); session.flush()
