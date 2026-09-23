"""Daily clean-up of tables that otherwise grow forever.

Runs automatically once a day from the monitoring thread (whichever worker
process gets there first -- the others see the timestamp and skip), or on
demand with `flask maintenance purge`. Retention periods are RETENTION_* in
project_settings.py. Deletes run in batches so a large first run doesn't
hold long table locks.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from flask import current_app

from app.extensions import db
from app.logging import logger
from app.models.ai_usage import AiUsageDaily
from app.models.audit_log import AuditLog
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.monitoring import ProcessHeartbeat, RequestMetric
from app.models.page_view import PageView
from app.models.site_setting import SiteSetting
from app.models.token_blacklist import TokenBlacklist

_LAST_RUN_KEY = 'maintenance_last_run'
_BATCH = 5000


def _delete_in_batches(model, condition) -> int:
    pk = sa.inspect(model).primary_key[0]
    deleted = 0
    while True:
        ids = [row[0] for row in db.session.query(pk).filter(condition).limit(_BATCH)]
        if not ids:
            return deleted
        db.session.query(model).filter(pk.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        deleted += len(ids)


def purge_old_data() -> dict:
    cfg = current_app.config
    now = datetime.now(timezone.utc)

    def ago(days: int) -> datetime:
        return now - timedelta(days=days)

    chat_cutoff = ago(cfg['RETENTION_CHAT_DAYS'])
    stale_sessions = db.session.query(ChatSession.id).filter(ChatSession.last_activity_at < chat_cutoff)

    counts = {
        'page_views': _delete_in_batches(PageView, PageView.created_at < ago(cfg['RETENTION_PAGE_VIEWS_DAYS'])),
        'chat_messages': _delete_in_batches(ChatMessage, ChatMessage.session_id.in_(stale_sessions.scalar_subquery())),
        'chat_sessions': _delete_in_batches(ChatSession, ChatSession.last_activity_at < chat_cutoff),
        # One audit row is written per chat message -- those are only worth
        # keeping as long as the chats themselves.
        'chat_audit_logs': _delete_in_batches(
            AuditLog, sa.and_(AuditLog.action == 'chat_message', AuditLog.created_at < chat_cutoff)),
        'audit_logs': _delete_in_batches(AuditLog, AuditLog.created_at < ago(cfg['RETENTION_AUDIT_DAYS'])),
        'request_metrics': _delete_in_batches(RequestMetric, RequestMetric.bucket < ago(cfg['RETENTION_METRICS_DAYS'])),
        'process_heartbeats': _delete_in_batches(ProcessHeartbeat, ProcessHeartbeat.last_seen < ago(2)),
        'token_blacklist': _delete_in_batches(TokenBlacklist, TokenBlacklist.expires_at < now),
        'ai_usage_daily': _delete_in_batches(AiUsageDaily, AiUsageDaily.day < ago(400).date()),
    }
    logger.info('maintenance_purge_done', **counts)
    return counts


def run_daily_if_due() -> None:
    last = SiteSetting.get(_LAST_RUN_KEY)
    now = datetime.now(timezone.utc)
    if last and now - datetime.fromisoformat(last) < timedelta(hours=23):
        return
    # Claim the run before doing it so other worker processes skip.
    SiteSetting.upsert(_LAST_RUN_KEY, now.isoformat())
    purge_old_data()
