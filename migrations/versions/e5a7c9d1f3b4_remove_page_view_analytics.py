"""remove page-view analytics table

The site's own page-view tracker (one database row per page a visitor
opened) has been removed. Downgrade recreates the table EMPTY -- the rows
themselves can only come back from a database backup.

Revision ID: e5a7c9d1f3b4
Revises: d4f6a8b0c2e3
Create Date: 2026-10-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'e5a7c9d1f3b4'
down_revision = 'd4f6a8b0c2e3'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('page_views')


def downgrade():
    op.create_table(
        'page_views',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('path', sa.String(length=1024), nullable=False),
        sa.Column('referrer', sa.String(length=1024), nullable=True),
        sa.Column('device_type', sa.String(length=20), nullable=True),
        sa.Column('browser', sa.String(length=50), nullable=True),
        sa.Column('os', sa.String(length=50), nullable=True),
        sa.Column('session_id', sa.String(length=64), nullable=True),
        sa.Column('ip_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_page_views_path', 'page_views', ['path'])
    op.create_index('ix_page_views_session_id', 'page_views', ['session_id'])
    op.create_index('ix_page_views_created_at', 'page_views', ['created_at'])
