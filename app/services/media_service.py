from __future__ import annotations

import io
import logging
import mimetypes
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from PIL import Image

log = logging.getLogger(__name__)

_ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
_CONTENT_TYPES = {
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png':  'image/png',
    '.webp': 'image/webp',
    '.gif':  'image/gif',
}

# Uploaded photos routinely arrive multi-megabyte (full-resolution camera/
# screenshot output) despite only ever being displayed as small cards —
# resize/recompress on upload so every visitor doesn't pay that cost.
_MAX_DIMENSION = 1920
_JPEG_QUALITY = 82

# Animated GIFs would break if resized frame-by-frame here — pass through as-is.
_RESIZABLE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}


def _optimize_image(data: bytes, ext: str) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            if img.width > _MAX_DIMENSION or img.height > _MAX_DIMENSION:
                img.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION), Image.LANCZOS)

            out = io.BytesIO()
            if ext in ('.jpg', '.jpeg'):
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                img.save(out, format='JPEG', quality=_JPEG_QUALITY, optimize=True)
            elif ext == '.png':
                img.save(out, format='PNG', optimize=True)
            elif ext == '.webp':
                img.save(out, format='WEBP', quality=_JPEG_QUALITY)
            else:
                return data
            return out.getvalue()
    except Exception:
        # If Pillow can't process it for any reason, fall back to the
        # original bytes rather than blocking the upload.
        log.warning('Image optimization failed, storing original bytes', exc_info=True)
        return data


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


def _company_files_dir() -> Path:
    """Separate, independently-configurable directory for company installer
    files (NinjaOne MSIs etc) -- these can be much larger than CVs/images and
    an operator may want them on a different disk/volume entirely, so this
    isn't just a subfolder of MEDIA_UPLOAD_DIR unless COMPANY_FILES_DIR is
    left unset."""
    raw = current_app.config.get('COMPANY_FILES_DIR', '')
    d = Path(raw) if raw else _media_dir() / 'company_files'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error('Cannot create COMPANY_FILES_DIR %s: %s', d, exc)
        raise
    return d


def save_image(data: bytes, original_ext: str) -> str:
    """Encrypt and persist image bytes. Returns the stored file_name (e.g. 'abc123.jpg')."""
    ext = original_ext.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise ValueError(f'Unsupported image type: {ext}')
    if ext in _RESIZABLE_EXTENSIONS:
        data = _optimize_image(data, ext)
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


_ALLOWED_COMPANY_FILE_EXTENSIONS = {'.msi'}
_COMPANY_FILE_CONTENT_TYPES = {'.msi': 'application/x-msi'}


def save_company_file(data: bytes, original_ext: str) -> str:
    """Encrypt and persist a company installer file. Stored under
    COMPANY_FILES_DIR, independent of MEDIA_UPLOAD_DIR. Returns the stored file_name."""
    ext = original_ext.lower()
    if ext not in _ALLOWED_COMPANY_FILE_EXTENSIONS:
        raise ValueError(f'Unsupported company file type: {ext}')
    file_id   = secrets.token_hex(16)
    file_name = f"{file_id}{ext}"
    enc_path  = _company_files_dir() / f"{file_name}.enc"
    enc_path.write_bytes(_fernet().encrypt(data))
    return file_name


def load_company_file(file_name: str) -> tuple[bytes, str] | None:
    """Decrypt and return (bytes, content_type) for a stored company file, or None if missing/invalid."""
    enc_path = _company_files_dir() / f"{file_name}.enc"
    if not enc_path.exists():
        return None
    try:
        raw = _fernet().decrypt(enc_path.read_bytes())
    except (InvalidToken, Exception):
        return None
    ext = Path(file_name).suffix.lower()
    content_type = _COMPANY_FILE_CONTENT_TYPES.get(ext, 'application/octet-stream')
    return raw, content_type


def delete_company_file(file_name: str) -> None:
    enc_path = _company_files_dir() / f"{file_name}.enc"
    enc_path.unlink(missing_ok=True)
