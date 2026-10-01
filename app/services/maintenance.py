"""Daily clean-up of tables that otherwise grow forever.

Run on demand with `flask maintenance purge` (or as a Plesk scheduled
task) -- nothing runs it automatically. Retention periods are RETENTION_* in
project_settings.py. Deletes run in batches so a large first run doesn't
hold long table locks.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from flask import current_app

from app.extensions import db
from app.logging import logger
from app.models.audit_log import AuditLog
from app.models.token_blacklist import TokenBlacklist

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

    counts = {
        'audit_logs': _delete_in_batches(AuditLog, AuditLog.created_at < ago(cfg['RETENTION_AUDIT_DAYS'])),
        'token_blacklist': _delete_in_batches(TokenBlacklist, TokenBlacklist.expires_at < now),
    }
    logger.info('maintenance_purge_done', **counts)
    return counts
