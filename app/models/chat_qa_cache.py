from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.extensions import db


class ChatQaCache(db.Model):
    """Reusable AI replies keyed by a normalized past question.

    Only ever populated from general Q&A exchanges (see chat_cache_service's
    eligibility checks) -- never from the ticket-collection flow, and never
    when the question itself looks like it contains an email/phone number.
    No visitor-identifying data is stored here, only the question text and
    the AI's generic reply.
    """

    __tablename__ = 'chat_qa_cache'

    id = db.Column(db.String(36), primary_key=True, default=lambda: uuid4().hex)
    question = db.Column(db.Text, nullable=False)
    question_normalized = db.Column(db.Text, nullable=False, index=True)
    reply = db.Column(db.Text, nullable=False)
    action = db.Column(db.String(20), nullable=True)
    action_payload = db.Column(db.JSON, nullable=True)
    model_name = db.Column(db.String(100), nullable=True)
    hit_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
