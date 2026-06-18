from __future__ import annotations

import queue
import threading

_lock: threading.Lock = threading.Lock()
_queues: list[queue.Queue[str]] = []


def subscribe() -> queue.Queue[str]:
    q: queue.Queue[str] = queue.Queue(maxsize=10)
    with _lock:
        _queues.append(q)
    return q


def unsubscribe(q: queue.Queue[str]) -> None:
    with _lock:
        try:
            _queues.remove(q)
        except ValueError:
            pass


def broadcast(message: str) -> None:
    with _lock:
        for q in list(_queues):
            try:
                q.put_nowait(message)
            except queue.Full:
                pass
