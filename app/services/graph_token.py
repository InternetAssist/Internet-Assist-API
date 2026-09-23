from __future__ import annotations

import threading
import time

import requests

_TOKEN_URL = 'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'

# App-only Graph tokens are valid for ~1 hour. Fetching a fresh one for every
# email / role check added a full round-trip to Azure AD to each request, so
# they're cached per (tenant, client) until shortly before expiry.
_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[str, float]] = {}
_EXPIRY_MARGIN_SECONDS = 120


def get_app_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    key = (tenant_id, client_id)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

    resp = requests.post(
        _TOKEN_URL.format(tenant_id=tenant_id),
        data={
            'grant_type':    'client_credentials',
            'client_id':     client_id,
            'client_secret': client_secret,
            'scope':         'https://graph.microsoft.com/.default',
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data['access_token']
    ttl = max(int(data.get('expires_in', 3600)) - _EXPIRY_MARGIN_SECONDS, 60)
    with _lock:
        _cache[key] = (token, now + ttl)
    return token
