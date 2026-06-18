from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class SiteSetting(db.Model, TimestampMixin):
    __tablename__ = 'site_settings'

    key   = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.JSON, nullable=True)

    @classmethod
    def get(cls, key: str, default=None):
        row = db.session.get(cls, key)
        return row.value if row else default

    @classmethod
    def upsert(cls, key: str, value) -> 'SiteSetting':
        row = db.session.get(cls, key)
        if row:
            row.value = value
        else:
            row = cls(key=key, value=value)
            db.session.add(row)
        db.session.commit()
        return row
