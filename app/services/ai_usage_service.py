from __future__ import annotations

from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.logging import logger
from app.models.ai_usage import AiUsageDaily

_COUNTERS = {'ai_calls', 'tokens_in', 'tokens_out', 'cache_hits', 'off_topic', 'local_replies', 'budget_blocked',
             'embedding_calls'}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def increment(**counts: int) -> None:
    """Atomically add to today's counters. Uses its own connection so it never
    commits (or rolls back) the caller's session, and an UPDATE ... SET x = x + n
    so concurrent worker processes don't lose counts. Call it after the
    caller's commit. A failure is logged, never raised -- a missed count
    mustn't break the chat reply."""
    try:
        _increment(counts)
    except Exception:
        logger.exception('ai_usage_increment_failed', counts=counts)


def _increment(counts: dict) -> None:
    counts = {k: v for k, v in counts.items() if k in _COUNTERS and v}
    if not counts:
        return
    table = AiUsageDaily.__table__
    day = _today()
    update = (
        table.update()
        .where(table.c.day == day)
        .values({k: table.c[k] + v for k, v in counts.items()})
    )
    with db.engine.begin() as conn:
        if conn.execute(update).rowcount:
            return
    row = {c: 0 for c in _COUNTERS} | counts | {'day': day}
    try:
        with db.engine.begin() as conn:
            conn.execute(table.insert().values(row))
    except IntegrityError:
        # Another process created today's row first.
        with db.engine.begin() as conn:
            conn.execute(update)


def today() -> dict:
    with db.engine.connect() as conn:
        row = conn.execute(sa.select(AiUsageDaily.__table__).where(AiUsageDaily.__table__.c.day == _today())).mappings().first()
    result = {c: 0 for c in _COUNTERS}
    if row:
        result.update({c: row[c] for c in _COUNTERS})
    return result


def last_days(days: int) -> list[dict]:
    table = AiUsageDaily.__table__
    with db.engine.connect() as conn:
        rows = conn.execute(sa.select(table).order_by(table.c.day.desc()).limit(days)).mappings().all()
    return [{'day': r['day'].isoformat(), **{c: r[c] for c in _COUNTERS}} for r in reversed(rows)]
