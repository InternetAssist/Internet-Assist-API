from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.dialects import mysql

from app.extensions import db


class SiteKnowledgeChunk(db.Model):
    """One searchable section of the public website (or a published blog
    post / job posting) that the chatbot answers from. Rebuilt wholesale by
    app/services/site_crawler.py -- never edited by hand."""

    __tablename__ = 'site_knowledge_chunks'

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    source     = db.Column(db.String(20), nullable=False)    # page | blog | job
    url        = db.Column(db.String(512), nullable=False)   # site-relative path, e.g. /cyber-security
    title      = db.Column(db.String(255), nullable=False)
    heading    = db.Column(db.String(255), nullable=True)
    content    = db.Column(db.Text, nullable=False)
    # Unit-length float32 meaning vector (embedding_service), or NULL when the
    # embedding API wasn't available at crawl time.
    embedding       = db.Column(db.LargeBinary().with_variant(mysql.MEDIUMBLOB(), 'mysql'), nullable=True)
    embedding_model = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
