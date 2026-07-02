from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.extensions import db


class Quote(db.Model):
    __tablename__ = 'quotes'

    id = db.Column(db.String(36), primary_key=True, default=lambda: uuid4().hex)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(255), nullable=False, index=True)
    phone = db.Column(db.String(50), nullable=True)
    company = db.Column(db.String(255), nullable=True)
    services = db.Column(db.JSON, nullable=False, default=list)
    team_size = db.Column(db.Integer, nullable=True)
    timeline = db.Column(db.String(255), nullable=True)
    details = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), default='pending', nullable=False, index=True)
    ticket_id = db.Column(db.String(36), nullable=True)
    ticket_ref = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
