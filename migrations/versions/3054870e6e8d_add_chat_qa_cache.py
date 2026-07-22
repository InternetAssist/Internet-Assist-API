"""add chat_qa_cache

Revision ID: 3054870e6e8d
Revises: 319db789e7c3
Create Date: 2026-07-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '3054870e6e8d'
down_revision = '319db789e7c3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'chat_qa_cache',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('question_normalized', sa.Text(), nullable=False),
        sa.Column('reply', sa.Text(), nullable=False),
        sa.Column('action', sa.String(length=20), nullable=True),
        sa.Column('action_payload', sa.JSON(), nullable=True),
        sa.Column('model_name', sa.String(length=100), nullable=True),
        sa.Column('hit_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_chat_qa_cache_question_normalized', 'chat_qa_cache', ['question_normalized'])


def downgrade():
    op.drop_index('ix_chat_qa_cache_question_normalized', table_name='chat_qa_cache')
    op.drop_table('chat_qa_cache')
