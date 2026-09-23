"""chatbot website knowledge, AI usage counters, request monitoring

Revision ID: a7c1e9d2b4f0
Revises: 3054870e6e8d
Create Date: 2026-09-23 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7c1e9d2b4f0'
down_revision = '3054870e6e8d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'site_knowledge_chunks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('url', sa.String(length=512), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('heading', sa.String(length=255), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'ai_usage_daily',
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('ai_calls', sa.Integer(), nullable=False),
        sa.Column('tokens_in', sa.Integer(), nullable=False),
        sa.Column('tokens_out', sa.Integer(), nullable=False),
        sa.Column('cache_hits', sa.Integer(), nullable=False),
        sa.Column('off_topic', sa.Integer(), nullable=False),
        sa.Column('local_replies', sa.Integer(), nullable=False),
        sa.Column('budget_blocked', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('day'),
    )

    op.create_table(
        'request_metrics',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bucket', sa.DateTime(timezone=True), nullable=False),
        sa.Column('endpoint', sa.String(length=160), nullable=False),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('status_class', sa.String(length=3), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('total_ms', sa.Float(), nullable=False),
        sa.Column('max_ms', sa.Float(), nullable=False),
        sa.Column('slow_count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bucket', 'endpoint', 'method', 'status_class', name='uq_request_metrics_bucket'),
    )
    op.create_index('ix_request_metrics_bucket', 'request_metrics', ['bucket'])

    op.create_table(
        'process_heartbeats',
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('host', sa.String(length=100), nullable=False),
        sa.Column('pid', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False),
        sa.Column('rss_mb', sa.Float(), nullable=False),
        sa.Column('peak_rss_mb', sa.Float(), nullable=False),
        sa.Column('cpu_percent', sa.Float(), nullable=False),
        sa.Column('threads', sa.Integer(), nullable=False),
        sa.Column('in_flight', sa.Integer(), nullable=False),
        sa.Column('requests_total', sa.Integer(), nullable=False),
        sa.Column('errors_total', sa.Integer(), nullable=False),
        sa.Column('background_pending', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_process_heartbeats_last_seen', 'process_heartbeats', ['last_seen'])

    op.add_column('chat_qa_cache', sa.Column('sources', sa.JSON(), nullable=True))
    op.add_column('chat_sessions', sa.Column('ai_calls', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('chat_sessions', sa.Column('last_relevant_at', sa.DateTime(timezone=True), nullable=True))

    # Replies cached before the chatbot was grounded on the website weren't
    # checked against it -- start the cache fresh.
    op.execute('DELETE FROM chat_qa_cache')


def downgrade():
    op.drop_column('chat_sessions', 'last_relevant_at')
    op.drop_column('chat_sessions', 'ai_calls')
    op.drop_column('chat_qa_cache', 'sources')
    op.drop_index('ix_process_heartbeats_last_seen', table_name='process_heartbeats')
    op.drop_table('process_heartbeats')
    op.drop_index('ix_request_metrics_bucket', table_name='request_metrics')
    op.drop_table('request_metrics')
    op.drop_table('ai_usage_daily')
    op.drop_table('site_knowledge_chunks')
