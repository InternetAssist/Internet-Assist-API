"""add service_type to projects

Revision ID: 8c371ae8f651
Revises: f7b8c9d0e1a2
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = '8c371ae8f651'
down_revision = 'f7b8c9d0e1a2'
branch_labels = None
depends_on = None

SERVICE_TAG_PREFIX = 'service:'


def upgrade():
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('service_type', sa.String(50), nullable=True))
        batch_op.create_index('ix_projects_service_type', ['service_type'])

    # Backfill: projects tagged "service:<slug>" (the old convention) get their
    # slug promoted into the new column, and the tag removed from the list.
    conn = op.get_bind()
    projects = sa.table(
        'projects',
        sa.column('id', sa.String),
        sa.column('tags', sa.JSON),
        sa.column('service_type', sa.String),
    )
    rows = conn.execute(sa.select(projects.c.id, projects.c.tags)).fetchall()
    for row in rows:
        tags = row.tags or []
        service_tag = next((t for t in tags if isinstance(t, str) and t.startswith(SERVICE_TAG_PREFIX)), None)
        if not service_tag:
            continue
        remaining_tags = [t for t in tags if t != service_tag]
        conn.execute(
            projects.update()
            .where(projects.c.id == row.id)
            .values(service_type=service_tag[len(SERVICE_TAG_PREFIX):], tags=remaining_tags)
        )


def downgrade():
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_index('ix_projects_service_type')
        batch_op.drop_column('service_type')
