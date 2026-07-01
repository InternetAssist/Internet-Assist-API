from __future__ import annotations

import json
import queue as _queue

from flask import Blueprint, Response, g, request, stream_with_context

from app.extensions import db
from app.models.site_setting import SiteSetting
from app.services import ai_config_service
from app.services.audit_service import log_audit_action
from app.services.sse_broadcast import broadcast, subscribe, unsubscribe
from app.utils.decorators import roles_required
from app.utils.response import envelope

blp = Blueprint('settings', __name__)

_SEASON_KEY = 'season'
_DEFAULT_SEASON: dict = {'enabled': True, 'override': 'winter'}
_VALID_OVERRIDES = {'auto', 'winter', 'spring', 'summer', 'autumn', 'off'}


def _current_season() -> dict:
    try:
        result = SiteSetting.get(_SEASON_KEY, _DEFAULT_SEASON)
        return result if result is not None else _DEFAULT_SEASON
    except Exception:
        db.session.rollback()
        return _DEFAULT_SEASON


# ── Public: read current season ───────────────────────────────────────────────

@blp.route('/settings/season')
def get_season():
    return envelope(data=_current_season(), status=200)


# ── Public: SSE stream — real-time season updates ─────────────────────────────

@blp.route('/settings/season/stream')
def season_stream():
    # Compute the initial value and immediately release the DB connection.
    # SSE streams are long-lived; holding a connection for their duration would
    # exhaust the pool under concurrent visitors and cause 500s on other routes.
    initial = json.dumps(_current_season())
    db.session.remove()

    q = subscribe()

    def generate():
        try:
            yield f"data: {initial}\n\n"
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield f"data: {msg}\n\n"
                except _queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            unsubscribe(q)

    return Response(
        stream_with_context(generate()),
        content_type='text/event-stream',
        headers={
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


# ── Admin: update season ──────────────────────────────────────────────────────

@blp.route('/admin/settings/season', methods=['GET'])
@roles_required('admin')
def admin_get_season():
    return envelope(data=_current_season(), status=200)


@blp.route('/admin/settings/season', methods=['PATCH'])
@roles_required('admin')
def patch_season():
    payload = request.get_json(silent=True) or {}
    current = _current_season()

    enabled  = payload.get('enabled',  current.get('enabled',  True))
    override = payload.get('override', current.get('override', 'winter'))

    if not isinstance(enabled, bool):
        return envelope(error={'code': 'invalid', 'message': '`enabled` must be a boolean', 'details': None}, status=422)
    if override not in _VALID_OVERRIDES:
        return envelope(
            error={'code': 'invalid', 'message': f'`override` must be one of {sorted(_VALID_OVERRIDES)}', 'details': None},
            status=422,
        )

    new_value = {'enabled': enabled, 'override': override}
    SiteSetting.upsert(_SEASON_KEY, new_value)
    broadcast(json.dumps(new_value))

    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_update_season',
        entity='site_setting',
        entity_id=_SEASON_KEY,
        diff={'old': current, 'new': new_value},
        ip=request.remote_addr,
    )
    return envelope(data=new_value, status=200)


# ── Admin: AI (Gemini) API key ─────────────────────────────────────────────────
# The key is never returned to the browser once saved — only whether one is set.

@blp.route('/admin/settings/ai', methods=['GET'])
@roles_required('admin')
def admin_get_ai_settings():
    return envelope(data={'configured': ai_config_service.is_configured()}, status=200)


@blp.route('/admin/settings/ai', methods=['PATCH'])
@roles_required('admin')
def patch_ai_settings():
    payload = request.get_json(silent=True) or {}
    api_key = (payload.get('api_key') or '').strip()
    if not api_key:
        return envelope(error={'code': 'invalid', 'message': 'api_key is required', 'details': None}, status=422)

    ai_config_service.set_api_key(api_key)
    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_update_ai_key',
        entity='site_setting',
        entity_id='ai_config',
        ip=request.remote_addr,
    )
    return envelope(data={'configured': True}, status=200)


@blp.route('/admin/settings/ai', methods=['DELETE'])
@roles_required('admin')
def delete_ai_settings():
    ai_config_service.clear_api_key()
    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_clear_ai_key',
        entity='site_setting',
        entity_id='ai_config',
        ip=request.remote_addr,
    )
    return envelope(data={'configured': False}, status=200)


# ── Chatbot widget on/off ───────────────────────────────────────────────────────
# Defaults to disabled — it's still under development and shouldn't appear for
# real visitors until explicitly turned on from Admin.

_CHATBOT_KEY = 'chatbot'
_DEFAULT_CHATBOT: dict = {'enabled': False}


def _current_chatbot() -> dict:
    try:
        result = SiteSetting.get(_CHATBOT_KEY, _DEFAULT_CHATBOT)
        return result if result is not None else _DEFAULT_CHATBOT
    except Exception:
        db.session.rollback()
        return _DEFAULT_CHATBOT


@blp.route('/settings/chatbot')
def get_chatbot_settings():
    return envelope(data=_current_chatbot(), status=200)


@blp.route('/admin/settings/chatbot', methods=['GET'])
@roles_required('admin')
def admin_get_chatbot_settings():
    return envelope(data=_current_chatbot(), status=200)


@blp.route('/admin/settings/chatbot', methods=['PATCH'])
@roles_required('admin')
def patch_chatbot_settings():
    payload = request.get_json(silent=True) or {}
    enabled = payload.get('enabled')
    if not isinstance(enabled, bool):
        return envelope(error={'code': 'invalid', 'message': '`enabled` must be a boolean', 'details': None}, status=422)

    current = _current_chatbot()
    new_value = {'enabled': enabled}
    SiteSetting.upsert(_CHATBOT_KEY, new_value)

    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_update_chatbot_enabled',
        entity='site_setting',
        entity_id=_CHATBOT_KEY,
        diff={'old': current, 'new': new_value},
        ip=request.remote_addr,
    )
    return envelope(data=new_value, status=200)
