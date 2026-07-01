from __future__ import annotations

from flask import current_app

from app.models.site_setting import SiteSetting
from app.services.crypto_service import decrypt_text, encrypt_text

_KEY = 'ai_config'


def is_configured() -> bool:
    stored = SiteSetting.get(_KEY, {}) or {}
    return bool(stored.get('api_key_encrypted'))


def set_api_key(api_key: str) -> None:
    SiteSetting.upsert(_KEY, {'api_key_encrypted': encrypt_text(api_key)})


def clear_api_key() -> None:
    SiteSetting.upsert(_KEY, {})


def resolve_api_key() -> str | None:
    """DB-stored key (set via Admin) takes priority; falls back to the env var."""
    stored = SiteSetting.get(_KEY, {}) or {}
    encrypted = stored.get('api_key_encrypted')
    if encrypted:
        decrypted = decrypt_text(encrypted)
        if decrypted:
            return decrypted
    return current_app.config.get('AI_API_KEY') or None
