"""Request metrics and process health, cheap enough to leave on in production.

Each worker process counts requests in memory per (minute, route, method,
status class) -- a dictionary update under a lock, no I/O on the request path.
A daemon thread flushes those counters to the database once a minute along
with a heartbeat of the process's memory/CPU/threads, and runs the daily data
clean-up. Because every process writes to the same tables, the Admin ->
Monitoring view shows the whole server no matter how many worker processes
Plesk / IIS / Passenger starts.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import psutil
import sqlalchemy as sa
from flask import Flask, current_app, g, request

from app.extensions import db
from app.logging import logger
from app.models.monitoring import ProcessHeartbeat, RequestMetric

_FLUSH_SECONDS = 60

_lock = threading.Lock()
_buckets: dict[tuple, list] = {}          # key -> [count, total_ms, max_ms, slow_count]
_totals = {'requests': 0, 'errors': 0, 'in_flight': 0}
_started_at = datetime.now(timezone.utc)
_flusher: threading.Thread | None = None
_peak_rss_mb = 0.0


def init_app(app: Flask) -> None:
    if not app.config.get('MONITORING_ENABLED'):
        return
    slow_ms = app.config['SLOW_REQUEST_MS']

    @app.before_request
    def _monitor_start():
        g._monitor_t0 = time.perf_counter()
        with _lock:
            _totals['in_flight'] += 1
        _ensure_flusher(app)

    @app.after_request
    def _monitor_record(response):
        t0 = getattr(g, '_monitor_t0', None)
        if t0 is None:
            return response
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # Route pattern, not the raw path, so /admin/jobs/<id> is one row.
        endpoint = (request.url_rule.rule if request.url_rule else '<unmatched>')[:160]
        status_class = f'{response.status_code // 100}xx'
        bucket = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        key = (bucket, endpoint, request.method, status_class)
        slow = elapsed_ms >= slow_ms
        with _lock:
            row = _buckets.setdefault(key, [0, 0.0, 0.0, 0])
            row[0] += 1
            row[1] += elapsed_ms
            row[2] = max(row[2], elapsed_ms)
            row[3] += int(slow)
            _totals['requests'] += 1
            if response.status_code >= 500:
                _totals['errors'] += 1
        if slow:
            logger.warning('slow_request', endpoint=endpoint, method=request.method,
                           status=response.status_code, ms=round(elapsed_ms))
        return response

    @app.teardown_request
    def _monitor_end(_exc):
        if getattr(g, '_monitor_t0', None) is not None:
            with _lock:
                _totals['in_flight'] -= 1


def _ensure_flusher(app: Flask) -> None:
    # Started lazily from the first request so it runs in the worker process
    # itself (not a parent that later forks).
    global _flusher
    if _flusher is not None and _flusher.is_alive():
        return
    with _lock:
        if _flusher is not None and _flusher.is_alive():
            return
        _flusher = threading.Thread(target=_flush_loop, args=(app,), name='monitoring-flush', daemon=True)
        _flusher.start()


def _flush_loop(app: Flask) -> None:
    from app.services import maintenance

    while True:
        time.sleep(_FLUSH_SECONDS)
        with app.app_context():
            try:
                flush()
            except Exception:
                logger.exception('monitoring_flush_failed')
            try:
                maintenance.run_daily_if_due()
            except Exception:
                logger.exception('maintenance_run_failed')
            finally:
                db.session.remove()


def _process_vitals() -> dict:
    global _peak_rss_mb
    proc = psutil.Process()
    rss_mb = proc.memory_info().rss / (1024 * 1024)
    _peak_rss_mb = max(_peak_rss_mb, rss_mb)
    return {
        'rss_mb': round(rss_mb, 1),
        'peak_rss_mb': round(_peak_rss_mb, 1),
        'cpu_percent': proc.cpu_percent(interval=None),
        'threads': proc.num_threads(),
    }


def flush() -> None:
    """Write this process's counters and heartbeat. Counters are swapped out
    first and dropped if the write fails, so memory stays bounded even when
    the database is unavailable."""
    from app.services import background

    global _buckets
    with _lock:
        data, _buckets = _buckets, {}
        totals = dict(_totals)

    vitals = _process_vitals()
    host = socket.gethostname()[:100]
    now = datetime.now(timezone.utc)
    heartbeat = {
        'host': host,
        'pid': os.getpid(),
        'started_at': _started_at,
        'last_seen': now,
        'in_flight': max(totals['in_flight'], 0),
        'requests_total': totals['requests'],
        'errors_total': totals['errors'],
        'background_pending': background.pending_count(),
        **vitals,
    }
    heartbeat_id = f'{host}:{os.getpid()}:{int(_started_at.timestamp())}'

    with db.engine.begin() as conn:
        for (bucket, endpoint, method, status_class), (count, total_ms, max_ms, slow) in data.items():
            _add_metric(conn, {
                'bucket': bucket, 'endpoint': endpoint, 'method': method, 'status_class': status_class,
                'count': count, 'total_ms': total_ms, 'max_ms': max_ms, 'slow_count': slow,
            })
        hb = ProcessHeartbeat.__table__
        if not conn.execute(hb.update().where(hb.c.id == heartbeat_id).values(heartbeat)).rowcount:
            conn.execute(hb.insert().values(id=heartbeat_id, **heartbeat))

    if vitals['rss_mb'] > current_app.config['MEMORY_WARN_MB']:
        logger.warning('process_memory_high', rss_mb=vitals['rss_mb'], pid=os.getpid(),
                       limit_mb=current_app.config['MEMORY_WARN_MB'])


def _add_metric(conn, row: dict) -> None:
    """Add to an existing minute row (another process may have written the
    same minute) or insert it."""
    t = RequestMetric.__table__
    match = sa.and_(t.c.bucket == row['bucket'], t.c.endpoint == row['endpoint'],
                    t.c.method == row['method'], t.c.status_class == row['status_class'])
    update = t.update().where(match).values(
        count=t.c.count + row['count'],
        total_ms=t.c.total_ms + row['total_ms'],
        max_ms=sa.case((t.c.max_ms < row['max_ms'], row['max_ms']), else_=t.c.max_ms),
        slow_count=t.c.slow_count + row['slow_count'],
    )
    if conn.execute(update).rowcount:
        return
    try:
        with conn.begin_nested():
            conn.execute(t.insert().values(row))
    except sa.exc.IntegrityError:
        conn.execute(update)


# ── Reading (Admin -> Monitoring) ─────────────────────────────────────────────

def summary(hours: int) -> dict:
    t = RequestMetric.__table__
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with db.engine.connect() as conn:
        by_bucket = conn.execute(
            sa.select(t.c.bucket, t.c.status_class, sa.func.sum(t.c.count), sa.func.sum(t.c.total_ms), sa.func.max(t.c.max_ms))
            .where(t.c.bucket >= since)
            .group_by(t.c.bucket, t.c.status_class)
        ).all()
        by_endpoint = conn.execute(
            sa.select(
                t.c.endpoint, t.c.method,
                sa.func.sum(t.c.count).label('count'),
                sa.func.sum(t.c.total_ms).label('total_ms'),
                sa.func.max(t.c.max_ms).label('max_ms'),
                sa.func.sum(t.c.slow_count).label('slow'),
                sa.func.sum(sa.case((t.c.status_class == '5xx', t.c.count), else_=0)).label('errors'),
                sa.func.sum(sa.case((t.c.status_class == '4xx', t.c.count), else_=0)).label('client_errors'),
            )
            .where(t.c.bucket >= since)
            .group_by(t.c.endpoint, t.c.method)
            .order_by(sa.func.sum(t.c.count).desc())
            .limit(100)
        ).all()

    # Series: 1-minute points for up to 2h, 15-minute points beyond that.
    step = 1 if hours <= 2 else 15
    series: dict[datetime, dict] = {}
    totals = {'requests': 0, 'errors': 0, 'client_errors': 0, 'total_ms': 0.0, 'max_ms': 0.0}
    for bucket, status_class, count, total_ms, max_ms in by_bucket:
        # MariaDB returns SUM() as Decimal; SQLite as int/float.
        count, total_ms, max_ms = int(count or 0), float(total_ms or 0), float(max_ms or 0)
        if bucket.tzinfo is None:
            bucket = bucket.replace(tzinfo=timezone.utc)
        slot = bucket.replace(minute=bucket.minute - bucket.minute % step)
        point = series.setdefault(slot, {'requests': 0, 'errors': 0, 'total_ms': 0.0})
        point['requests'] += count
        point['total_ms'] += total_ms or 0
        totals['requests'] += count
        totals['total_ms'] += total_ms or 0
        totals['max_ms'] = max(totals['max_ms'], max_ms or 0)
        if status_class == '5xx':
            point['errors'] += count
            totals['errors'] += count
        elif status_class == '4xx':
            totals['client_errors'] += count

    return {
        'window_hours': hours,
        'step_minutes': step,
        'totals': {
            'requests': totals['requests'],
            'errors': totals['errors'],
            'client_errors': totals['client_errors'],
            'error_rate': round(totals['errors'] / totals['requests'] * 100, 2) if totals['requests'] else 0,
            'avg_ms': round(totals['total_ms'] / totals['requests'], 1) if totals['requests'] else 0,
            'max_ms': round(totals['max_ms'], 1),
            'requests_per_minute': round(totals['requests'] / (hours * 60), 2),
        },
        'series': [
            {'t': slot.isoformat(), 'requests': p['requests'], 'errors': p['errors'],
             'avg_ms': round(p['total_ms'] / p['requests'], 1) if p['requests'] else 0}
            for slot, p in sorted(series.items())
        ],
        'endpoints': [
            {'endpoint': r.endpoint, 'method': r.method, 'count': int(r.count), 'errors': int(r.errors or 0),
             'client_errors': int(r.client_errors or 0), 'slow': int(r.slow or 0),
             'avg_ms': round(float(r.total_ms or 0) / int(r.count), 1) if r.count else 0,
             'max_ms': round(float(r.max_ms or 0), 1)}
            for r in by_endpoint
        ],
    }


def live_processes() -> list[dict]:
    """Processes that reported in the last 3 minutes."""
    t = ProcessHeartbeat.__table__
    since = datetime.now(timezone.utc) - timedelta(minutes=3)
    with db.engine.connect() as conn:
        rows = conn.execute(sa.select(t).where(t.c.last_seen >= since).order_by(t.c.started_at)).mappings().all()
    out = []
    for r in rows:
        started = r['started_at'] if r['started_at'].tzinfo else r['started_at'].replace(tzinfo=timezone.utc)
        last_seen = r['last_seen'] if r['last_seen'].tzinfo else r['last_seen'].replace(tzinfo=timezone.utc)
        out.append({
            'host': r['host'], 'pid': r['pid'],
            'uptime_minutes': int((datetime.now(timezone.utc) - started).total_seconds() // 60),
            'last_seen': last_seen.isoformat(),
            'rss_mb': r['rss_mb'], 'peak_rss_mb': r['peak_rss_mb'], 'cpu_percent': r['cpu_percent'],
            'threads': r['threads'], 'in_flight': r['in_flight'],
            'requests_total': r['requests_total'], 'errors_total': r['errors_total'],
            'background_pending': r['background_pending'],
        })
    return out


def system_vitals(paths: list[str]) -> dict:
    mem = psutil.virtual_memory()
    disks = []
    for p in paths:
        try:
            usage = psutil.disk_usage(p)
            disks.append({'path': p, 'free_gb': round(usage.free / 1024 ** 3, 1), 'percent_used': usage.percent})
        except OSError:
            continue
    pool = db.engine.pool
    return {
        'memory_percent': mem.percent,
        'memory_available_mb': round(mem.available / (1024 * 1024)),
        'memory_total_mb': round(mem.total / (1024 * 1024)),
        'cpu_percent': psutil.cpu_percent(interval=None),
        'cpu_count': psutil.cpu_count(),
        'disks': disks,
        # This process's DB connection pool.
        'db_pool': {
            'size': getattr(pool, 'size', lambda: None)(),
            'checked_out': getattr(pool, 'checkedout', lambda: None)(),
            'overflow': getattr(pool, 'overflow', lambda: None)(),
        },
    }
