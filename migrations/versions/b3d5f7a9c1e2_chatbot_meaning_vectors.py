"""chatbot meaning vectors (embeddings) and embedding call counter

Revision ID: b3d5f7a9c1e2
Revises: a7c1e9d2b4f0
Create Date: 2026-09-23 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = 'b3d5f7a9c1e2'
down_revision = 'a7c1e9d2b4f0'
branch_labels = None
depends_on = None


def upgrade():
    # 768 float32s = 3 KB per section; plain BLOB caps at 64 KB on MariaDB.
    op.add_column('site_knowledge_chunks', sa.Column(
        'embedding', sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), 'mysql'), nullable=True))
    op.add_column('site_knowledge_chunks', sa.Column('embedding_model', sa.String(length=100), nullable=True))
    op.add_column('ai_usage_daily', sa.Column('embedding_calls', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('ai_usage_daily', 'embedding_calls')
    op.drop_column('site_knowledge_chunks', 'embedding_model')
    op.drop_column('site_knowledge_chunks', 'embedding')
