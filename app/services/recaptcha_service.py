from __future__ import annotations

import logging

import requests
from flask import current_app

log = logging.getLogger(__name__)

_VERIFY_URL = 'https://www.google.com/recaptcha/api/siteverify'
_SCORE_THRESHOLD = 0.5


def verify_recaptcha(token: str, remote_ip: str | None = None) -> bool:
    """Verify a reCAPTCHA v3 token. Returns True if the submission should be
    allowed through -- including when RECAPTCHA_SECRET_KEY isn't configured,
    so local dev and testing work without real Google API keys."""
    secret = current_app.config.get('RECAPTCHA_SECRET_KEY', '')
    if not secret:
        return True
    if not token:
        # Fail open on a *missing* token rather than reject outright. A
        # basic bot that doesn't execute the page's JS at all never reaches
        # this far -- the honeypot field already catches it. A missing
        # token here more often means a real visitor's ad blocker/privacy
        # extension silently stopped Google's script from loading, and
        # silently dropping their enquiry (this returns a fake "success" to
        # the caller either way) is worse than letting one slip through.
        # A *present* token with a low bot-likelihood score below still
        # gets rejected -- that's a real signal, not an infra hiccup.
        return True

    try:
        resp = requests.post(
            _VERIFY_URL,
            data={'secret': secret, 'response': token, 'remoteip': remote_ip},
            timeout=5,
        )
        data = resp.json()
    except Exception as exc:
        # Network hiccup talking to Google -- fail open rather than block
        # every public form submission over a transient outage.
        log.warning('reCAPTCHA verification request failed: %s', exc)
        return True

    if not data.get('success'):
        return False
    return data.get('score', 0.0) >= _SCORE_THRESHOLD
