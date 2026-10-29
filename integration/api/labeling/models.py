"""Labeling records. Every tenant-owned table carries `tenant_id`; children reference parents by (tenant_id, id)
so the database itself rejects cross-tenant references. CVAT ids are scoped by `cvat_instance`, never primary keys."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Stamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TenantCvatOrg(Base, Stamped):
    """tenant -> CVAT organization (one per tenant per CVAT instance). Every CVAT lookup is scoped through this."""

    __tablename__ = "tenant_cvat_org"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cvat_instance: Mapped[str] = mapped_column(String(64), primary_key=True)
    cvat_org_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cvat_org_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    cvat_webhook_id: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("cvat_instance", "cvat_org_id"), UniqueConstraint("cvat_instance", "cvat_webhook_id", name="uq_tenant_cvat_org_webhook"), UniqueConstraint("cvat_instance", "cvat_org_slug"))


class CvatIdentityMapping(Base, Stamped):
    __tablename__ = "cvat_identity_mapping"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cvat_instance: Mapped[str] = mapped_column(String(64), nullable=False)
    cvat_user_id: Mapped[int | None] = mapped_column(Integer)
    cvat_username: Mapped[str | None] = mapped_column(String(150))
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # worker|supervisor|maintainer|owner
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|active|revoked
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "cvat_instance"),
        UniqueConstraint("cvat_instance", "cvat_user_id", "tenant_id"),
        ForeignKeyConstraint(["tenant_id", "cvat_instance"], ["tenant_cvat_org.tenant_id", "tenant_cvat_org.cvat_instance"]),
    )


class LabelSchemaVersion(Base, Stamped):
    __tablename__ = "label_schema_version"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    semver: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "id"), UniqueConstraint("tenant_id", "semver"),
                      UniqueConstraint("tenant_id", "content_hash"))


class LabelingProject(Base, Stamped):
    __tablename__ = "labeling_project"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    cvat_instance: Mapped[str] = mapped_column(String(64), nullable=False)
    cvat_project_id: Mapped[int | None] = mapped_column(Integer)
    default_team: Mapped[list | None] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "schema_version_id"], ["label_schema_version.tenant_id", "label_schema_version.id"]),
    )


class LabelingBatch(Base, Stamped):
    __tablename__ = "labeling_batch"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    media_manifest: Mapped[list] = mapped_column(JSONB, nullable=False)
    review_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=lambda: {"mode": "independent_all"})
    instructions_version: Mapped[str | None] = mapped_column(String(64))
    predecessor_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # revision batch: the version it revises
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lifecycle: Mapped[str] = mapped_column(String(24), nullable=False, default="provisioning")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "project_id"], ["labeling_project.tenant_id", "labeling_project.id"]),
    )


class LabelingTask(Base, Stamped):
    __tablename__ = "labeling_task"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_asset_id: Mapped[str] = mapped_column(String(256), nullable=False)
    clip: Mapped[dict] = mapped_column(JSONB, nullable=False)  # MediaReference (immutable content ref + sha256)
    cvat_instance: Mapped[str] = mapped_column(String(64), nullable=False)
    cvat_task_id: Mapped[int | None] = mapped_column(Integer)
    frame_map: Mapped[dict | None] = mapped_column(JSONB)
    segment_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|ingesting|ready|failed
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "batch_id", "source_asset_id"),
        ForeignKeyConstraint(["tenant_id", "batch_id"], ["labeling_batch.tenant_id", "labeling_batch.id"]),
        Index("uq_task_cvat_ref", "cvat_instance", "cvat_task_id", unique=True, postgresql_where=text("cvat_task_id IS NOT NULL")),
    )


class LabelingJob(Base, Stamped):
    __tablename__ = "labeling_job"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    cvat_instance: Mapped[str] = mapped_column(String(64), nullable=False)
    cvat_job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    start_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    assignee_user_id: Mapped[str | None] = mapped_column(String(128))
    portal_state: Mapped[str] = mapped_column(String(24), nullable=False, default="created")
    cvat_stage: Mapped[str | None] = mapped_column(String(16))
    cvat_state: Mapped[str | None] = mapped_column(String(16))
    cvat_updated_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    annotation_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    labeler_user_id: Mapped[str | None] = mapped_column(String(128))  # annotator of record (reviewer must differ)
    reviewer_user_id: Mapped[str | None] = mapped_column(String(128))
    approved_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("cvat_instance", "cvat_job_id"),
        ForeignKeyConstraint(["tenant_id", "task_id"], ["labeling_task.tenant_id", "labeling_task.id"]),
        Index("ix_job_tenant_state", "tenant_id", "portal_state"),
    )


class Operation(Base, Stamped):
    """Durable, resumable unit of work. `progress` records each completed step (+ CVAT ids) so a retry resumes."""

    __tablename__ = "operation"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # provision_batch|reconcile|...
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # e.g. batch id
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    progress: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[dict | None] = mapped_column(JSONB)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "idempotency_key"), Index("ix_operation_queue", "state", "run_after"))


class WebhookInbox(Base):
    __tablename__ = "webhook_inbox"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cvat_instance: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(300), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("cvat_instance", "dedupe_key"), Index("ix_inbox_pending", "processed_at"))


class Outbox(Base):
    """Mirrors the marketplace `outbox_events` columns so incorporation is a table move."""

    __tablename__ = "outbox_events"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LabelingMember(Base, Stamped):
    """Standalone stand-in for marketplace tenant membership: who may use labeling in a tenant, and as what.
    Identity key is the IdP `sub` (Cognito). Replaced by the host IdentityContext binding at incorporation."""

    __tablename__ = "labeling_member"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False)  # labeler|reviewer|creator|admin
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active|removed
    username: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(254))
    __table_args__ = (Index("ix_member_user", "user_id"),)


class AnnotationSnapshot(Base):
    """Immutable (DB trigger forbids UPDATE/DELETE): the exact annotations of a job at a review decision."""

    __tablename__ = "annotation_snapshot"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    annotation_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(24), nullable=False)  # submit|reject|approve|reopen|restore
    format: Mapped[str] = mapped_column(String(48), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "job_id"], ["labeling_job.tenant_id", "labeling_job.id"]),
                      Index("ix_snapshot_job", "tenant_id", "job_id", "created_at"))


class ReviewEvent(Base):
    """Append-only audit of every review command (DB trigger forbids UPDATE/DELETE)."""

    __tablename__ = "review_event"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)  # submit|resubmit|reject|approve|reopen|approval_invalidated
    reason: Mapped[str | None] = mapped_column(Text)
    annotation_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # issue ids, attestation, override
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "job_id"], ["labeling_job.tenant_id", "labeling_job.id"]),
                      Index("ix_review_event_job", "tenant_id", "job_id", "created_at"))


class ReviewIssue(Base, Stamped):
    __tablename__ = "review_issue"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    frame: Mapped[int] = mapped_column(Integer, nullable=False)  # CVAT task frame index
    position: Mapped[list | None] = mapped_column(JSONB)  # [x1, y1, x2, y2]
    text: Mapped[str] = mapped_column(Text, nullable=False)
    blocking: Mapped[bool] = mapped_column(nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="open")  # open|resolved
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(String(128))
    cvat_issue_id: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "job_id"], ["labeling_job.tenant_id", "labeling_job.id"]),
                      Index("ix_issue_job", "tenant_id", "job_id", "status"))


class DatasetVersion(Base, Stamped):
    """One published version per send-off. The id is allocated when the send-off is created so replays reuse it."""

    __tablename__ = "dataset_version"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    predecessor_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="publishing")  # publishing|published
    revision_set: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # job id -> approved snapshot sha256
    manifest_uri: Mapped[str | None] = mapped_column(Text)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    catalog_ack: Mapped[str | None] = mapped_column(String(256))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("tenant_id", "batch_id"), UniqueConstraint("tenant_id", "id"),
                      ForeignKeyConstraint(["tenant_id", "batch_id"], ["labeling_batch.tenant_id", "labeling_batch.id"]))


class PrelabelRun(Base, Stamped):
    """One pre-label run: pinned model identity + settings, and a summary that keeps failure and zero detections apart."""

    __tablename__ = "prelabel_run"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_descriptor: Mapped[dict] = mapped_column(JSONB, nullable=False)  # pinned artifact identity, mapping, preprocessing
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")  # queued|running|succeeded|failed|cancelled|manual_fallback
    cancel_requested: Mapped[bool] = mapped_column(nullable=False, default=False)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("tenant_id", "id"),
                      ForeignKeyConstraint(["tenant_id", "batch_id"], ["labeling_batch.tenant_id", "labeling_batch.id"]),
                      Index("ix_prelabel_run_batch", "tenant_id", "batch_id", "status"))


class PrelabelChunk(Base, Stamped):
    """A bounded group of frames sent as one inference request. `inference_id` is deterministic, so re-submission is safe."""

    __tablename__ = "prelabel_chunk"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    frames: Mapped[list] = mapped_column(JSONB, nullable=False)  # CVAT frame indexes
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")  # pending|submitted|collected|failed
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inference_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_uri: Mapped[str | None] = mapped_column(Text)
    raw_uri: Mapped[str | None] = mapped_column(Text)
    normalized_uri: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("run_id", "task_id", "seq"),
                      ForeignKeyConstraint(["tenant_id", "run_id"], ["prelabel_run.tenant_id", "prelabel_run.id"]),
                      Index("ix_prelabel_chunk_status", "run_id", "status"))


class PrelabelImport(Base, Stamped):
    """Import ledger: one row per (run, task). `jobs` maps job id -> sha256 of the job's annotations right after import,
    which is how a retry tells 'already imported' from 'a human has edited since' without ever overwriting work."""

    __tablename__ = "prelabel_import"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")  # pending|done|empty|conflict
    shapes_expected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    baseline_uri: Mapped[str | None] = mapped_column(Text)
    baseline_sha256: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "run_id"], ["prelabel_run.tenant_id", "prelabel_run.id"]),)
