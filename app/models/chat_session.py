from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.extensions import db


class ChatSession(db.Model):
    __tablename__ = 'chat_sessions'

    id = db.Column(db.String(36), primary_key=True, default=lambda: uuid4().hex)
    started_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_activity_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    messages = db.relationship(
        'ChatMessage',
        back_populates='session',
        cascade='all, delete-orphan',
        order_by='ChatMessage.created_at.asc()',
    )
    # Ticket creation flow state captured for interactive chat ticketing
    ticket_flow_state = db.Column(db.String(50), nullable=True)
    ticket_flow_data = db.Column(db.JSON, nullable=True)
