"""pre-label runs, chunks and import ledger

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def _stamps():
    return [sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.create_table(
        'prelabel_run',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', sa.String(128), nullable=False),
        sa.Column('batch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('model_id', sa.String(128), nullable=False),
        sa.Column('model_descriptor', postgresql.JSONB, nullable=False),
        sa.Column('settings', postgresql.JSONB, nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='queued'),
        sa.Column('cancel_requested', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('operation_id', postgresql.UUID(as_uuid=True)),
        sa.Column('created_by', sa.String(128), nullable=False),
        sa.Column('summary', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('error', sa.Text),
        *_stamps(),
        sa.UniqueConstraint('tenant_id', 'id'),
        sa.ForeignKeyConstraint(['tenant_id', 'batch_id'], ['labeling_batch.tenant_id', 'labeling_batch.id']),
    )
    op.create_index('ix_prelabel_run_batch', 'prelabel_run', ['tenant_id', 'batch_id', 'status'])
    op.create_table(
        'prelabel_chunk',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', sa.String(128), nullable=False),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('seq', sa.Integer, nullable=False),
        sa.Column('frames', postgresql.JSONB, nullable=False),
        sa.Column('status', sa.String(12), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer, nullable=False, server_default='0'),
        sa.Column('inference_id', sa.String(64), nullable=False),
        sa.Column('input_uri', sa.Text),
        sa.Column('raw_uri', sa.Text),
        sa.Column('normalized_uri', sa.Text),
        sa.Column('error', sa.Text),
        *_stamps(),
        sa.UniqueConstraint('run_id', 'task_id', 'seq'),
        sa.ForeignKeyConstraint(['tenant_id', 'run_id'], ['prelabel_run.tenant_id', 'prelabel_run.id']),
    )
    op.create_index('ix_prelabel_chunk_status', 'prelabel_chunk', ['run_id', 'status'])
    op.create_table(
        'prelabel_import',
        sa.Column('tenant_id', sa.String(128), primary_key=True),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('status', sa.String(12), nullable=False, server_default='pending'),
        sa.Column('shapes_expected', sa.Integer, nullable=False, server_default='0'),
        sa.Column('jobs', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('baseline_uri', sa.Text),
        sa.Column('baseline_sha256', sa.String(64)),
        sa.Column('error', sa.Text),
        *_stamps(),
        sa.ForeignKeyConstraint(['tenant_id', 'run_id'], ['prelabel_run.tenant_id', 'prelabel_run.id']),
    )


def downgrade() -> None:
    op.drop_table('prelabel_import')
    op.drop_table('prelabel_chunk')
    op.drop_table('prelabel_run')
