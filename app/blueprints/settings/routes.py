from __future__ import annotations

from flask import Blueprint, g, request

from app.extensions import db
from app.models.site_setting import SiteSetting
from app.services import ai_config_service
from app.services.audit_service import log_audit_action
from app.utils.decorators import roles_required
from app.utils.response import envelope

blp = Blueprint('settings', __name__)


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
