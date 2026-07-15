"""add blog_posts table

Revision ID: 319db789e7c3
Revises: a3f8c1d92e04
Create Date: 2026-07-15 00:00:00.000001

"""
from alembic import op
import sqlalchemy as sa

revision = '319db789e7c3'
down_revision = 'a3f8c1d92e04'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('blog_posts',
        sa.Column('id',                   sa.String(36), nullable=False),
        sa.Column('title',                sa.String(255), nullable=False),
        sa.Column('slug',                 sa.String(255), nullable=False),
        sa.Column('excerpt',              sa.Text(), nullable=True),
        sa.Column('body',                 sa.Text(), nullable=False),
        sa.Column('author_name',          sa.String(150), nullable=True),
        sa.Column('tags',                 sa.JSON(), nullable=True),
        sa.Column('cover_image_url',      sa.String(1024), nullable=True),
        sa.Column('cover_image_file_id',  sa.String(150), nullable=True),
        sa.Column('status',               sa.String(30), nullable=False, server_default='draft'),
        sa.Column('published_at',         sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at',           sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at',           sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_blog_posts_status', 'blog_posts', ['status'], unique=False)
    op.create_index('ix_blog_posts_slug', 'blog_posts', ['slug'], unique=True)


def downgrade():
    op.drop_index('ix_blog_posts_slug', table_name='blog_posts')
    op.drop_index('ix_blog_posts_status', table_name='blog_posts')
    op.drop_table('blog_posts')
