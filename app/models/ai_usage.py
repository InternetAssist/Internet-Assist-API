from __future__ import annotations

from app.extensions import db


class AiUsageDaily(db.Model):
    """Per-day chatbot counters, shared by every worker process -- this is
    what enforces CHAT_MAX_AI_CALLS_PER_DAY and what the Monitoring tab shows."""

    __tablename__ = 'ai_usage_daily'

    day             = db.Column(db.Date, primary_key=True)
    ai_calls        = db.Column(db.Integer, nullable=False, default=0)
    tokens_in       = db.Column(db.Integer, nullable=False, default=0)
    tokens_out      = db.Column(db.Integer, nullable=False, default=0)
    cache_hits      = db.Column(db.Integer, nullable=False, default=0)
    off_topic       = db.Column(db.Integer, nullable=False, default=0)
    local_replies   = db.Column(db.Integer, nullable=False, default=0)   # greetings etc, no AI
    budget_blocked  = db.Column(db.Integer, nullable=False, default=0)
    embedding_calls = db.Column(db.Integer, nullable=False, default=0, server_default='0')
