from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app

from app.models.site_setting import SiteSetting

_STATUS_KEY = 'ai_last_status'


def is_configured() -> bool:
    return bool(current_app.config.get('AI_API_KEY'))


def resolve_api_key() -> str | None:
    return current_app.config.get('AI_API_KEY') or None


def record_call_result(success: bool, detail: str | None = None) -> None:
    """Tracks the outcome of the most recent real Gemini call.

    "Configured" (is_configured) only means a key is present -- it doesn't
    mean the key actually works right now (wrong key, expired billing, zero
    quota, etc. all still count as "configured"). This is what the admin
    dashboard's live status indicator reads, updated by service.py after
    every real chat request that reaches the AI branch -- no extra API
    calls are spent just to populate this.
    """
    SiteSetting.upsert(_STATUS_KEY, {
        'success': success,
        'detail': detail,
        'checked_at': datetime.now(timezone.utc).isoformat(),
    })


def last_status() -> dict | None:
    return SiteSetting.get(_STATUS_KEY)
