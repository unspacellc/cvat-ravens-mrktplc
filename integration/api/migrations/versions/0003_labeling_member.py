"""labeling member

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'labeling_member',
        sa.Column('tenant_id', sa.String(128), nullable=False),
        sa.Column('user_id', sa.String(128), nullable=False),
        sa.Column('roles', postgresql.ARRAY(sa.String(16)), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='active'),
        sa.Column('username', sa.String(150)),
        sa.Column('email', sa.String(254)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id', 'user_id'),
    )
    op.create_index('ix_member_user', 'labeling_member', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_member_user', table_name='labeling_member')
    op.drop_table('labeling_member')
