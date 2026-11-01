"""rename outbox_events -> labeling_outbox (the marketplace already owns a different `outbox_events` table)

Revision ID: 0007
Revises: 0006
"""
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table('outbox_events', 'labeling_outbox')


def downgrade() -> None:
    op.rename_table('labeling_outbox', 'outbox_events')
