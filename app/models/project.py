from __future__ import annotations

from uuid import uuid4

from app.models.base import BaseModel
from app.extensions import db

SERVICE_TYPES = (
    'it-support',
    'cloud-services',
    'cyber-security',
    'backup-recovery',
    'communications',
    'infrastructure',
    'software-development',
    'web-design',
)


class Project(BaseModel):
    __tablename__ = 'projects'

    id         = db.Column(db.String(36), primary_key=True, default=lambda: uuid4().hex)
    title      = db.Column(db.String(255), nullable=False)
    client     = db.Column(db.String(255), nullable=True)
    industry   = db.Column(db.String(100), nullable=True)
    summary    = db.Column(db.Text, nullable=False)
    challenge  = db.Column(db.Text, nullable=True)
    solution   = db.Column(db.Text, nullable=True)
    outcome    = db.Column(db.Text, nullable=True)
    tags       = db.Column(db.JSON, nullable=True)
    service_type = db.Column(db.String(50), nullable=True, index=True)
    image_url   = db.Column(db.String(1024), nullable=True)
    project_url = db.Column(db.String(1024), nullable=True)
    status        = db.Column(db.String(30),  nullable=False, default='draft', index=True)
    image_file_id = db.Column(db.String(150), nullable=True)
