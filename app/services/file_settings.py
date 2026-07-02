from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from flask import current_app

log = logging.getLogger(__name__)

_lock = threading.Lock()

_DEFAULTS: dict = {
    'season': {'enabled': True, 'override': 'auto'},
    'chatbot': {'enabled': False},
    'enquiry_forwarding': {'enabled': True},
}


def _settings_dir() -> Path:
    raw = current_app.config.get('SITE_SETTINGS_DIR', '')
    d = Path(raw) if raw else Path(__file__).parent.parent.parent / 'site_settings'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error('Cannot create SITE_SETTINGS_DIR %s: %s', d, exc)
        raise
    return d


def _file_path() -> Path:
    return _settings_dir() / 'settings.json'


def _read_all() -> dict:
    try:
        with open(_file_path(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except OSError as exc:
        log.error('Cannot read site settings file: %s', exc)
        return {}


def _write_all(data: dict) -> None:
    path = _file_path()
    tmp_path = path.with_suffix('.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    tmp_path.replace(path)


def get(key: str) -> dict:
    with _lock:
        data = _read_all()
    return data.get(key, _DEFAULTS.get(key, {}))


def set(key: str, value: dict) -> None:
    with _lock:
        data = _read_all()
        data[key] = value
        _write_all(data)
