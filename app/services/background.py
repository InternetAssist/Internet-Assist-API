from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from flask import current_app, has_request_context, request

from app.logging import logger

# A small, bounded pool for slow side-effects (emails, re-indexing the
# chatbot's knowledge) so they don't hold a WSGI thread -- and the visitor --
# while Microsoft Graph or a crawl takes its time. Created lazily so each
# worker process (Passenger / HttpPlatformHandler may start several) gets its
# own pool after it has started.

_MAX_PENDING = 200

_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
_pending = 0


def _get_executor(workers: int) -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=max(workers, 1), thread_name_prefix='bg')
        return _executor


def pending_count() -> int:
    return _pending


def submit(fn, *args, **kwargs) -> None:
    """Run fn(*args, **kwargs) inside an app context on the background pool.
    Failures are logged, never raised. Runs inline under TESTING, and inline
    if the queue is already full, so work is never silently dropped."""
    global _pending
    app = current_app._get_current_object()
    name = getattr(fn, '__name__', 'task')
    # Keep the caller's host so code that builds absolute URLs from
    # request.host_url (e.g. the email logo) still works off-request.
    base_url = request.host_url if has_request_context() else None

    def run():
        global _pending
        try:
            ctx = app.test_request_context(base_url=base_url) if base_url else app.app_context()
            with ctx:
                fn(*args, **kwargs)
        except Exception:
            logger.exception('background_task_failed', task=name)
        finally:
            with _lock:
                _pending -= 1

    with _lock:
        _pending += 1
        queue_full = _pending > _MAX_PENDING

    if app.config.get('TESTING') or queue_full:
        if queue_full:
            logger.warning('background_queue_full_running_inline', task=name)
        run()
        return
    _get_executor(app.config.get('BACKGROUND_WORKERS', 2)).submit(run)
