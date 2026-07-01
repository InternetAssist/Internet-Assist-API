from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


def _fernet() -> Fernet:
    key = current_app.config.get('MEDIA_ENCRYPTION_KEY', '')
    if not key:
        raise RuntimeError('MEDIA_ENCRYPTION_KEY is not configured')
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_text(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_text(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, Exception):
        return None
