from __future__ import annotations

from uuid import uuid4

from app.models.base import BaseModel
from app.extensions import db


class BlogPost(BaseModel):
    __tablename__ = 'blog_posts'

    id            = db.Column(db.String(36), primary_key=True, default=lambda: uuid4().hex)
    title         = db.Column(db.String(255), nullable=False)
    slug          = db.Column(db.String(255), nullable=False, unique=True, index=True)
    excerpt       = db.Column(db.Text, nullable=True)
    body          = db.Column(db.Text, nullable=False)
    author_name   = db.Column(db.String(150), nullable=True)
    tags          = db.Column(db.JSON, nullable=True)
    cover_image_url     = db.Column(db.String(1024), nullable=True)
    cover_image_file_id = db.Column(db.String(150), nullable=True)
    status        = db.Column(db.String(30), nullable=False, default='draft', index=True)
    published_at  = db.Column(db.DateTime(timezone=True), nullable=True)
