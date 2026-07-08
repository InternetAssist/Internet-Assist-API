from __future__ import annotations

from uuid import uuid4

from app.extensions import db
from app.models.base import BaseModel


class Company(BaseModel):
    __tablename__ = 'companies'

    id     = db.Column(db.String(36), primary_key=True, default=lambda: uuid4().hex)
    name   = db.Column(db.String(255), nullable=False, unique=True, index=True)
    notes  = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='active', nullable=False, index=True)

    files = db.relationship('CompanyFile', backref='company', cascade='all, delete-orphan', lazy='dynamic')
