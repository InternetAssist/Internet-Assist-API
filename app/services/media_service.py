from __future__ import annotations

import logging
import mimetypes
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

log = logging.getLogger(__name__)

_ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
_CONTENT_TYPES = {
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png':  'image/png',
    '.webp': 'image/webp',
    '.gif':  'image/gif',
}

_ALLOWED_DOC_EXTENSIONS = {'.pdf', '.doc', '.docx'}
_DOC_CONTENT_TYPES = {
    '.pdf':  'application/pdf',
    '.doc':  'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
}


def _fernet() -> Fernet:
    key = current_app.config.get('MEDIA_ENCRYPTION_KEY', '')
    if not key:
        raise RuntimeError('MEDIA_ENCRYPTION_KEY is not configured')
    return Fernet(key.encode() if isinstance(key, str) else key)


def _media_dir() -> Path:
    raw = current_app.config.get('MEDIA_UPLOAD_DIR', '')
    d = Path(raw) if raw else Path(__file__).parent.parent.parent / 'media'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error('Cannot create MEDIA_UPLOAD_DIR %s: %s', d, exc)
        raise
    return d


def save_image(data: bytes, original_ext: str) -> str:
    """Encrypt and persist image bytes. Returns the stored file_name (e.g. 'abc123.jpg')."""
    ext = original_ext.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise ValueError(f'Unsupported image type: {ext}')
    file_id   = secrets.token_hex(16)
    file_name = f"{file_id}{ext}"
    enc_path  = _media_dir() / f"{file_name}.enc"
    enc_path.write_bytes(_fernet().encrypt(data))
    return file_name


def load_image(file_name: str) -> tuple[bytes, str] | None:
    """Decrypt and return (bytes, content_type), or None if missing / invalid."""
    enc_path = _media_dir() / f"{file_name}.enc"
    if not enc_path.exists():
        return None
    try:
        raw = _fernet().decrypt(enc_path.read_bytes())
    except (InvalidToken, Exception):
        return None
    ext = Path(file_name).suffix.lower()
    content_type = _CONTENT_TYPES.get(ext, 'application/octet-stream')
    return raw, content_type


def delete_image(file_name: str) -> None:
    enc_path = _media_dir() / f"{file_name}.enc"
    enc_path.unlink(missing_ok=True)


def save_document(data: bytes, original_ext: str) -> str:
    """Encrypt and persist document bytes (PDF/DOC/DOCX). Returns the stored file_name."""
    ext = original_ext.lower()
    if ext not in _ALLOWED_DOC_EXTENSIONS:
        raise ValueError(f'Unsupported document type: {ext}')
    file_id   = secrets.token_hex(16)
    file_name = f"{file_id}{ext}"
    enc_path  = _media_dir() / f"{file_name}.enc"
    enc_path.write_bytes(_fernet().encrypt(data))
    return file_name


def load_document(file_name: str) -> tuple[bytes, str] | None:
    """Decrypt and return (bytes, content_type) for a stored document, or None if missing/invalid."""
    enc_path = _media_dir() / f"{file_name}.enc"
    if not enc_path.exists():
        return None
    try:
        raw = _fernet().decrypt(enc_path.read_bytes())
    except (InvalidToken, Exception):
        return None
    ext = Path(file_name).suffix.lower()
    content_type = _DOC_CONTENT_TYPES.get(ext, 'application/octet-stream')
    return raw, content_type


def delete_document(file_name: str) -> None:
    enc_path = _media_dir() / f"{file_name}.enc"
    enc_path.unlink(missing_ok=True)
