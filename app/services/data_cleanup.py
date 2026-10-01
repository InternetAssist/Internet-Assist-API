"""Admin -> Settings -> Data clean-up: permanently delete contacts, quotes
and/or audit log entries, optionally only those older than N days.

Runs only when an admin asks for it (nothing here runs automatically).
Deletes in batches so a large clean-up doesn't hold long table locks.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.contact import Contact
from app.models.quote import Quote

TARGETS = {
    'contacts': Contact,      # includes remote-support requests (stored as contacts)
    'quotes': Quote,
    'audit_logs': AuditLog,
}
AGE_OPTIONS_DAYS = (30, 90, 180, 365)
CONFIRM_PHRASE = 'DELETE'
_BATCH = 2000


def _condition(model, older_than_days: int | None):
    if older_than_days is None:
        return sa.true()
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    return model.created_at < cutoff


def counts(older_than_days: int | None) -> dict[str, int]:
    """How many rows each target has (in total, or older than the cutoff)."""
    return {
        name: db.session.query(sa.func.count(model.id)).filter(_condition(model, older_than_days)).scalar() or 0
        for name, model in TARGETS.items()
    }


def delete(targets: list[str], older_than_days: int | None) -> dict[str, int]:
    """Delete rows of the given targets. Returns rows deleted per target."""
    deleted = {}
    for name in targets:
        model = TARGETS[name]
        condition = _condition(model, older_than_days)
        total = 0
        while True:
            ids = [row[0] for row in db.session.query(model.id).filter(condition).limit(_BATCH)]
            if not ids:
                break
            db.session.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()
            total += len(ids)
        deleted[name] = total
    return deleted
