from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.extensions import db


class JobApplication(db.Model):
    __tablename__ = 'job_applications'

    id = db.Column(db.String(36), primary_key=True, default=lambda: uuid4().hex)
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(255), nullable=False, index=True)
    phone = db.Column(db.String(50), nullable=True)
    position = db.Column(db.String(255), nullable=False)
    cover_letter = db.Column(db.Text, nullable=True)
    cv_blob_url = db.Column(db.String(1024), nullable=True)
    status = db.Column(db.String(30), default='new', nullable=False, index=True)
    ticket_id = db.Column(db.String(36), nullable=True)
    ticket_ref = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
