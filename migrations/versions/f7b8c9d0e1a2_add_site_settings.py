"""add site_settings table

Revision ID: f7b8c9d0e1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-06-18

"""
from alembic import op
import sqlalchemy as sa

revision = 'f7b8c9d0e1a2'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'site_settings',
        sa.Column('key',        sa.String(100), primary_key=True),
        sa.Column('value',      sa.JSON(),      nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), onupdate=sa.func.now()),
    )


def downgrade():
    op.drop_table('site_settings')
