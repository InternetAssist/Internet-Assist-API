from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.extensions import db
from app.models.role import user_roles


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.String(36), primary_key=True, default=lambda: uuid4().hex)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    roles = db.relationship('Role', secondary=user_roles, back_populates='users', lazy='joined')

    def has_role(self, *role_names: str) -> bool:
        return any(role.name in role_names for role in self.roles)
