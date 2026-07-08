from __future__ import annotations

from uuid import uuid4

from app.extensions import db
from app.models.base import BaseModel


class CompanyFile(BaseModel):
    __tablename__ = 'company_files'

    id                = db.Column(db.String(36), primary_key=True, default=lambda: uuid4().hex)
    company_id        = db.Column(db.String(36), db.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False, index=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_file_id    = db.Column(db.String(150), nullable=False)
    file_size         = db.Column(db.Integer, nullable=False)
    description       = db.Column(db.String(500), nullable=True)
    uploaded_by       = db.Column(db.String(255), nullable=True)
    status            = db.Column(db.String(20), default='active', nullable=False, index=True)
