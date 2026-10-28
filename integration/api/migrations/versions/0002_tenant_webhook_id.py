"""tenant webhook id

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tenant_cvat_org', sa.Column('cvat_webhook_id', sa.Integer(), nullable=True))
    op.create_unique_constraint('uq_tenant_cvat_org_webhook', 'tenant_cvat_org', ['cvat_instance', 'cvat_webhook_id'])


def downgrade() -> None:
    op.drop_constraint('uq_tenant_cvat_org_webhook', 'tenant_cvat_org', type_='unique')
    op.drop_column('tenant_cvat_org', 'cvat_webhook_id')
