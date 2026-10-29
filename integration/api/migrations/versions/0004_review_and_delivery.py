"""review events, snapshots, issues, dataset versions

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def _stamps():
    return [sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.add_column('labeling_batch', sa.Column('predecessor_version_id', postgresql.UUID(as_uuid=True)))
    op.add_column('labeling_job', sa.Column('labeler_user_id', sa.String(128)))
    op.add_column('labeling_job', sa.Column('reviewer_user_id', sa.String(128)))
    op.add_column('labeling_job', sa.Column('approved_snapshot_id', postgresql.UUID(as_uuid=True)))

    op.create_table(
        'annotation_snapshot',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', sa.String(128), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('annotation_revision', sa.Integer, nullable=False),
        sa.Column('reason', sa.String(24), nullable=False),
        sa.Column('format', sa.String(48), nullable=False),
        sa.Column('uri', sa.Text, nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('bytes', sa.Integer, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'job_id'], ['labeling_job.tenant_id', 'labeling_job.id']),
    )
    op.create_index('ix_snapshot_job', 'annotation_snapshot', ['tenant_id', 'job_id', 'created_at'])

    op.create_table(
        'review_event',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', sa.String(128), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('actor_id', sa.String(128), nullable=False),
        sa.Column('decision', sa.String(24), nullable=False),
        sa.Column('reason', sa.Text),
        sa.Column('annotation_revision', sa.Integer, nullable=False),
        sa.Column('snapshot_id', postgresql.UUID(as_uuid=True)),
        sa.Column('details', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'job_id'], ['labeling_job.tenant_id', 'labeling_job.id']),
    )
    op.create_index('ix_review_event_job', 'review_event', ['tenant_id', 'job_id', 'created_at'])

    # Evidence is append-only at the database level, not just by convention.
    op.execute("""
        CREATE FUNCTION labeling_forbid_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END $$ LANGUAGE plpgsql;
        CREATE TRIGGER annotation_snapshot_immutable BEFORE UPDATE OR DELETE ON annotation_snapshot
            FOR EACH ROW EXECUTE FUNCTION labeling_forbid_mutation();
        CREATE TRIGGER review_event_immutable BEFORE UPDATE OR DELETE ON review_event
            FOR EACH ROW EXECUTE FUNCTION labeling_forbid_mutation();
    """)

    op.create_table(
        'review_issue',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', sa.String(128), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('frame', sa.Integer, nullable=False),
        sa.Column('position', postgresql.JSONB),
        sa.Column('text', sa.Text, nullable=False),
        sa.Column('blocking', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('status', sa.String(12), nullable=False, server_default='open'),
        sa.Column('created_by', sa.String(128), nullable=False),
        sa.Column('resolved_by', sa.String(128)),
        sa.Column('cvat_issue_id', sa.Integer),
        *_stamps(),
        sa.ForeignKeyConstraint(['tenant_id', 'job_id'], ['labeling_job.tenant_id', 'labeling_job.id']),
    )
    op.create_index('ix_issue_job', 'review_issue', ['tenant_id', 'job_id', 'status'])

    op.create_table(
        'dataset_version',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', sa.String(128), nullable=False),
        sa.Column('batch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('operation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('predecessor_version_id', postgresql.UUID(as_uuid=True)),
        sa.Column('status', sa.String(16), nullable=False, server_default='publishing'),
        sa.Column('revision_set', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('manifest_uri', sa.Text),
        sa.Column('manifest_sha256', sa.String(64)),
        sa.Column('catalog_ack', sa.String(256)),
        sa.Column('published_at', sa.DateTime(timezone=True)),
        *_stamps(),
        sa.UniqueConstraint('tenant_id', 'batch_id'),
        sa.UniqueConstraint('tenant_id', 'id'),
        sa.ForeignKeyConstraint(['tenant_id', 'batch_id'], ['labeling_batch.tenant_id', 'labeling_batch.id']),
    )


def downgrade() -> None:
    op.drop_table('dataset_version')
    op.drop_table('review_issue')
    op.execute("DROP TRIGGER review_event_immutable ON review_event; DROP TRIGGER annotation_snapshot_immutable ON annotation_snapshot;")
    op.drop_table('review_event')
    op.drop_table('annotation_snapshot')
    op.execute("DROP FUNCTION labeling_forbid_mutation()")
    for c in ('approved_snapshot_id', 'reviewer_user_id', 'labeler_user_id'):
        op.drop_column('labeling_job', c)
    op.drop_column('labeling_batch', 'predecessor_version_id')
