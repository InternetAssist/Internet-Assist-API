from __future__ import annotations

import logging
import re
import time

import requests

log = logging.getLogger(__name__)

_TOR_LIST_URL = 'https://check.torproject.org/torbulkexitlist'
_TOR_CACHE_TTL_SECONDS = 6 * 3600  # Tor's exit list changes constantly; a few hours' staleness is fine for this use.

_tor_cache: dict = {'ips': set(), 'fetched_at': 0.0}

_URL_RE = re.compile(r'https?://|www\.', re.IGNORECASE)
_MAX_LINKS = 2  # legitimate enquiries essentially never contain more than one or two links


def _refresh_tor_list() -> None:
    try:
        resp = requests.get(_TOR_LIST_URL, timeout=8)
        resp.raise_for_status()
        ips = {line.strip() for line in resp.text.splitlines() if line.strip()}
        if ips:
            _tor_cache['ips'] = ips
            _tor_cache['fetched_at'] = time.time()
            log.info('Tor exit list refreshed: %d addresses', len(ips))
    except Exception as exc:
        # Keep serving whatever was cached before (or empty/fail-open if this
        # is the very first fetch) rather than blocking every submission
        # because Tor's list server had a bad moment.
        log.warning('Tor exit list refresh failed: %s', exc)


def is_tor_exit_node(ip: str | None) -> bool:
    """Known spam pattern for this site's forms: submissions from Tor exit
    nodes. A B2B IT-support contact form has essentially zero legitimate use
    of Tor, so this is a high-confidence, low-collateral-damage signal."""
    if not ip:
        return False
    if time.time() - _tor_cache['fetched_at'] > _TOR_CACHE_TTL_SECONDS:
        _refresh_tor_list()
    return ip in _tor_cache['ips']


def has_excessive_links(text: str | None) -> bool:
    """Classic, low-false-positive spam signal -- real enquiries don't come
    stuffed with links; SEO/pharma/casino spam almost always does."""
    if not text:
        return False
    return len(_URL_RE.findall(text)) > _MAX_LINKS
