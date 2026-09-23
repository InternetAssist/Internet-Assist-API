from __future__ import annotations

from app.extensions import db


class RequestMetric(db.Model):
    """Per-minute request counters, aggregated in memory by each worker
    process and flushed once a minute (app/services/monitoring.py)."""

    __tablename__ = 'request_metrics'
    __table_args__ = (
        db.UniqueConstraint('bucket', 'endpoint', 'method', 'status_class', name='uq_request_metrics_bucket'),
    )

    id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    bucket       = db.Column(db.DateTime(timezone=True), nullable=False, index=True)  # start of the minute, UTC
    endpoint     = db.Column(db.String(160), nullable=False)
    method       = db.Column(db.String(10), nullable=False)
    status_class = db.Column(db.String(3), nullable=False)    # 2xx | 3xx | 4xx | 5xx
    count        = db.Column(db.Integer, nullable=False, default=0)
    total_ms     = db.Column(db.Float, nullable=False, default=0)
    max_ms       = db.Column(db.Float, nullable=False, default=0)
    slow_count   = db.Column(db.Integer, nullable=False, default=0)


class ProcessHeartbeat(db.Model):
    """Latest vitals of each running API worker process."""

    __tablename__ = 'process_heartbeats'

    id             = db.Column(db.String(100), primary_key=True)   # host:pid:start
    host           = db.Column(db.String(100), nullable=False)
    pid            = db.Column(db.Integer, nullable=False)
    started_at     = db.Column(db.DateTime(timezone=True), nullable=False)
    last_seen      = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    rss_mb         = db.Column(db.Float, nullable=False, default=0)
    peak_rss_mb    = db.Column(db.Float, nullable=False, default=0)
    cpu_percent    = db.Column(db.Float, nullable=False, default=0)
    threads        = db.Column(db.Integer, nullable=False, default=0)
    in_flight      = db.Column(db.Integer, nullable=False, default=0)
    requests_total = db.Column(db.Integer, nullable=False, default=0)
    errors_total   = db.Column(db.Integer, nullable=False, default=0)
    background_pending = db.Column(db.Integer, nullable=False, default=0)
