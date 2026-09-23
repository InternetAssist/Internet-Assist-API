"""Meaning vectors (embeddings) from the Gemini API, so the chatbot matches
what a visitor *means* rather than the exact words they used -- "how fast do
your engineers turn up?" finds the section about response times.

Designed to fail soft. When there's no key, the API errors, or it's slow,
every function returns None and callers fall back to keyword search. After a
failure the API is left alone for a minute (circuit breaker) so visitors
aren't each made to wait on a dead endpoint. No local model -- nothing here
adds meaningful memory to the worker process.
"""
from __future__ import annotations

import math
import threading
import time
from array import array
from collections import OrderedDict

import httpx
from flask import current_app

from app.logging import logger

_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}'
_BATCH_SIZE = 100
_QUERY_TIMEOUT = httpx.Timeout(4.0, connect=2.0)     # a visitor is waiting
_INDEX_TIMEOUT = httpx.Timeout(30.0, connect=5.0)    # background re-index
_BREAKER_SECONDS = 60
_QUERY_CACHE_SIZE = 1000

_lock = threading.Lock()
_client: httpx.Client | None = None
_down_until = 0.0
_query_cache: OrderedDict[str, array] = OrderedDict()


def _http() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(limits=httpx.Limits(max_connections=5, max_keepalive_connections=2))
        return _client


def model_name() -> str:
    return current_app.config['EMBEDDING_MODEL']


def is_available() -> bool:
    return bool(current_app.config.get('AI_API_KEY')) and time.monotonic() >= _down_until


def _trip(reason: str) -> None:
    global _down_until
    _down_until = time.monotonic() + _BREAKER_SECONDS
    logger.warning('embedding_api_unavailable', reason=reason[:200], retry_in_s=_BREAKER_SECONDS)


def _normalise(values: list[float]) -> array:
    # Cosine similarity then becomes a plain dot product. Required for
    # gemini-embedding-001 below full size; harmless for already-unit vectors.
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return array('f', (v / norm for v in values))


def _request_item(text: str, is_query: bool, title: str | None) -> dict:
    model = model_name()
    item = {
        'model': f'models/{model}',
        'outputDimensionality': current_app.config['EMBEDDING_DIM'],
    }
    if model.endswith('-001'):
        item['taskType'] = 'RETRIEVAL_QUERY' if is_query else 'RETRIEVAL_DOCUMENT'
        item['content'] = {'parts': [{'text': text}]}
    else:
        # gemini-embedding-2 takes the task as a text prefix instead.
        prefixed = f'task: search result | query: {text}' if is_query else f'title: {title or "none"} | text: {text}'
        item['content'] = {'parts': [{'text': prefixed}]}
    return item


def _batch(items: list[dict], timeout: httpx.Timeout) -> list[array]:
    resp = _http().post(
        _URL.format(model=model_name(), method='batchEmbedContents'),
        headers={'X-goog-api-key': current_app.config['AI_API_KEY']},
        json={'requests': items},
        timeout=timeout,
    )
    resp.raise_for_status()
    embeddings = resp.json()['embeddings']
    if len(embeddings) != len(items):
        raise ValueError(f'expected {len(items)} embeddings, got {len(embeddings)}')
    return [_normalise(e['values']) for e in embeddings]


def embed_query(text: str) -> array | None:
    """Vector for a visitor's question, or None if the API can't be used
    right now. Repeated questions are served from memory."""
    key = ' '.join(text.lower().split())
    with _lock:
        cached = _query_cache.get(key)
        if cached is not None:
            _query_cache.move_to_end(key)
            return cached
    if not is_available():
        return None
    try:
        vector = _batch([_request_item(text, True, None)], _QUERY_TIMEOUT)[0]
    except Exception as exc:
        _trip(str(exc))
        return None
    from app.services import ai_usage_service
    ai_usage_service.increment(embedding_calls=1)
    with _lock:
        _query_cache[key] = vector
        if len(_query_cache) > _QUERY_CACHE_SIZE:
            _query_cache.popitem(last=False)
    return vector


def embed_documents(docs: list[tuple[str | None, str]]) -> list[array] | None:
    """Vectors for (title, text) pairs, in order -- or None if any batch
    fails (the caller keeps keyword search for this crawl)."""
    if not current_app.config.get('AI_API_KEY'):
        return None
    out: list[array] = []
    try:
        for start in range(0, len(docs), _BATCH_SIZE):
            items = [_request_item(text, False, title) for title, text in docs[start:start + _BATCH_SIZE]]
            out.extend(_batch(items, _INDEX_TIMEOUT))
    except Exception as exc:
        _trip(str(exc))
        return None
    from app.services import ai_usage_service
    ai_usage_service.increment(embedding_calls=-(-len(docs) // _BATCH_SIZE))
    return out


def to_bytes(vector: array) -> bytes:
    return vector.tobytes()


def from_bytes(blob: bytes | None) -> array | None:
    if not blob:
        return None
    vector = array('f')
    vector.frombytes(blob)
    return vector


def cosine(a: array, b: array) -> float:
    # Both unit length, so the dot product is the cosine similarity.
    return sum(x * y for x, y in zip(a, b))
