"""remove chatbot and monitoring tables

The chatbot moved out of this project (see IA-Chatbot-Engine) and monitoring
no longer records anything, so their tables and settings rows are dropped.
Downgrade recreates the tables EMPTY -- the rows themselves can only come back
from a database backup.

Revision ID: d4f6a8b0c2e3
Revises: b3d5f7a9c1e2
Create Date: 2026-10-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = 'd4f6a8b0c2e3'
down_revision = 'b3d5f7a9c1e2'
branch_labels = None
depends_on = None

_SETTING_KEYS = (
    'ai_last_status', 'chat_cache_config', 'chat_knowledge_crawl_started',
    'chat_knowledge_status', 'chat_semantic_thresholds', 'maintenance_last_run',
)


def upgrade():
    op.drop_index('ix_process_heartbeats_last_seen', table_name='process_heartbeats')
    op.drop_table('process_heartbeats')
    op.drop_index('ix_request_metrics_bucket', table_name='request_metrics')
    op.drop_table('request_metrics')
    op.drop_table('ai_usage_daily')
    op.drop_table('site_knowledge_chunks')
    op.drop_table('chat_qa_cache')
    op.drop_table('chat_messages')       # before chat_sessions (foreign key)
    op.drop_table('chat_sessions')

    keys = ', '.join(f"'{k}'" for k in _SETTING_KEYS)
    op.execute(f'DELETE FROM site_settings WHERE `key` IN ({keys})'
               if op.get_bind().dialect.name == 'mysql'
               else f'DELETE FROM site_settings WHERE "key" IN ({keys})')
    # One audit row was written per chat message.
    op.execute("DELETE FROM audit_logs WHERE action = 'chat_message'")


def downgrade():
    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ticket_flow_state', sa.String(length=50), nullable=True),
        sa.Column('ticket_flow_data', sa.JSON(), nullable=True),
        sa.Column('ai_calls', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_relevant_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'chat_messages',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tokens_in', sa.Integer(), nullable=False),
        sa.Column('tokens_out', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_chat_messages_session_id', 'chat_messages', ['session_id'])
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
        sa.Column('sources', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'site_knowledge_chunks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('url', sa.String(length=512), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('heading', sa.String(length=255), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('embedding', sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), 'mysql'), nullable=True),
        sa.Column('embedding_model', sa.String(length=100), nullable=True),
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
        sa.Column('embedding_calls', sa.Integer(), nullable=False, server_default='0'),
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
