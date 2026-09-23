from __future__ import annotations

import io
import logging
import mimetypes
import secrets
import struct
from pathlib import Path
from typing import BinaryIO, Iterator

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
    # CVs share this directory and this is served by a public route -- only
    # ever hand back image files from here.
    if Path(file_name).suffix.lower() not in _ALLOWED_EXTENSIONS:
        return None
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


# Company installers can be up to 250 MB. Fernet encrypts a whole message in
# memory, so they're stored as a sequence of independently encrypted 1 MB
# chunks: upload and download each hold ~2 MB at a time instead of several
# copies of the whole file. Each chunk's plaintext starts with its index and a
# "last chunk" flag, so chunks can't be reordered, dropped or truncated
# without decryption failing. Files written before this format (a single
# Fernet token, no magic header) are still readable.
_CHUNKED_MAGIC = b'IAENC2\n'
_CHUNK_SIZE = 1024 * 1024
_CHUNK_HEADER = struct.Struct('>QB')   # chunk index, is-final flag
_CHUNK_LEN = struct.Struct('>I')


class FileTooLargeError(Exception):
    pass


def save_company_file_stream(stream: BinaryIO, original_ext: str, max_bytes: int) -> tuple[str, int]:
    """Encrypt and persist a company installer read from `stream` in chunks.
    Returns (stored file_name, plaintext size). Raises FileTooLargeError past
    max_bytes (nothing is left on disk)."""
    ext = original_ext.lower()
    if ext not in _ALLOWED_COMPANY_FILE_EXTENSIONS:
        raise ValueError(f'Unsupported company file type: {ext}')
    fernet = _fernet()
    file_name = f"{secrets.token_hex(16)}{ext}"
    enc_path = _company_files_dir() / f"{file_name}.enc"
    tmp_path = enc_path.with_name(enc_path.name + '.part')

    total = 0
    index = 0
    try:
        with open(tmp_path, 'wb') as out:
            out.write(_CHUNKED_MAGIC)
            chunk = stream.read(_CHUNK_SIZE)
            while True:
                total += len(chunk)
                if total > max_bytes:
                    raise FileTooLargeError()
                nxt = stream.read(_CHUNK_SIZE) if chunk else b''
                token = fernet.encrypt(_CHUNK_HEADER.pack(index, 0 if nxt else 1) + chunk)
                out.write(_CHUNK_LEN.pack(len(token)))
                out.write(token)
                index += 1
                if not nxt:
                    break
                chunk = nxt
        tmp_path.replace(enc_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return file_name, total


def open_company_file(file_name: str) -> tuple[Iterator[bytes], str] | None:
    """Return (iterator of decrypted chunks, content_type), or None if the file
    is missing. Decryption happens lazily as the response is streamed."""
    enc_path = _company_files_dir() / f"{file_name}.enc"
    if not enc_path.exists():
        return None
    fernet = _fernet()
    content_type = _COMPANY_FILE_CONTENT_TYPES.get(Path(file_name).suffix.lower(), 'application/octet-stream')

    with open(enc_path, 'rb') as f:
        chunked = f.read(len(_CHUNKED_MAGIC)) == _CHUNKED_MAGIC
    if not chunked:
        try:
            return iter([fernet.decrypt(enc_path.read_bytes())]), content_type
        except InvalidToken:
            log.error('Company file %s failed to decrypt', file_name)
            return None

    def chunks() -> Iterator[bytes]:
        with open(enc_path, 'rb') as f:
            f.seek(len(_CHUNKED_MAGIC))
            expected = 0
            while True:
                raw_len = f.read(_CHUNK_LEN.size)
                if len(raw_len) != _CHUNK_LEN.size:
                    raise InvalidToken('company file truncated')
                plain = fernet.decrypt(f.read(_CHUNK_LEN.unpack(raw_len)[0]))
                index, final = _CHUNK_HEADER.unpack_from(plain)
                if index != expected:
                    raise InvalidToken('company file chunks out of order')
                yield plain[_CHUNK_HEADER.size:]
                if final:
                    return
                expected += 1

    return chunks(), content_type


def delete_company_file(file_name: str) -> None:
    enc_path = _company_files_dir() / f"{file_name}.enc"
    enc_path.unlink(missing_ok=True)
