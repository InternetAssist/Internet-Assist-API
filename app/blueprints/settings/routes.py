from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, request
from sqlalchemy import func, text

from app.extensions import db
from app.models.chat_qa_cache import ChatQaCache
from app.models.chat_session import ChatSession
from app.services import ai_config_service, file_settings
from app.services.audit_service import log_audit_action
from app.utils.decorators import roles_required
from app.utils.response import envelope

blp = Blueprint('settings', __name__)

_VALID_OVERRIDES = {'auto', 'winter', 'spring', 'summer', 'autumn', 'off'}


# ── Season ambience — stored in a flat JSON file, not the DB ──────────────────
# Decorative and rarely changed, so it doesn't need a DB row or a live SSE
# push. Clients cache the value locally and only re-check occasionally.

@blp.route('/settings/season')
def get_season():
    return envelope(data=file_settings.get('season'), status=200)


@blp.route('/admin/settings/season', methods=['GET'])
@roles_required('admin')
def admin_get_season():
    return envelope(data=file_settings.get('season'), status=200)


@blp.route('/admin/settings/season', methods=['PATCH'])
@roles_required('admin')
def patch_season():
    payload = request.get_json(silent=True) or {}
    current = file_settings.get('season')

    enabled  = payload.get('enabled',  current.get('enabled',  True))
    override = payload.get('override', current.get('override', 'auto'))

    if not isinstance(enabled, bool):
        return envelope(error={'code': 'invalid', 'message': '`enabled` must be a boolean', 'details': None}, status=422)
    if override not in _VALID_OVERRIDES:
        return envelope(
            error={'code': 'invalid', 'message': f'`override` must be one of {sorted(_VALID_OVERRIDES)}', 'details': None},
            status=422,
        )

    new_value = {'enabled': enabled, 'override': override}
    file_settings.set('season', new_value)

    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_update_season',
        entity='site_setting',
        entity_id='season',
        diff={'old': current, 'new': new_value},
        ip=request.remote_addr,
    )
    return envelope(data=new_value, status=200)


# ── Chatbot widget on/off — same flat-file approach as season ─────────────────
# Defaults to disabled — it's still under development and shouldn't appear for
# real visitors until explicitly turned on from Admin.

@blp.route('/settings/chatbot')
def get_chatbot_settings():
    return envelope(data=file_settings.get('chatbot'), status=200)


@blp.route('/admin/settings/chatbot', methods=['GET'])
@roles_required('admin')
def admin_get_chatbot_settings():
    return envelope(data=file_settings.get('chatbot'), status=200)


@blp.route('/admin/settings/chatbot', methods=['PATCH'])
@roles_required('admin')
def patch_chatbot_settings():
    payload = request.get_json(silent=True) or {}
    enabled = payload.get('enabled')
    if not isinstance(enabled, bool):
        return envelope(error={'code': 'invalid', 'message': '`enabled` must be a boolean', 'details': None}, status=422)

    current = file_settings.get('chatbot')
    new_value = {'enabled': enabled}
    file_settings.set('chatbot', new_value)

    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_update_chatbot_enabled',
        entity='site_setting',
        entity_id='chatbot',
        diff={'old': current, 'new': new_value},
        ip=request.remote_addr,
    )
    return envelope(data=new_value, status=200)


# ── Enquiry forwarding on/off — same flat-file approach as season/chatbot ─────
# Gates whether Contact/Quote/Job Application submissions email the enquiry
# inbox (NOTIFY_EMAIL_1/2). The underlying form/ticket submission itself is
# unaffected either way -- this only controls the extra email notification.

@blp.route('/admin/settings/enquiry-forwarding', methods=['GET'])
@roles_required('admin')
def admin_get_enquiry_forwarding():
    return envelope(data=file_settings.get('enquiry_forwarding'), status=200)


@blp.route('/admin/settings/enquiry-forwarding', methods=['PATCH'])
@roles_required('admin')
def patch_enquiry_forwarding():
    payload = request.get_json(silent=True) or {}
    enabled = payload.get('enabled')
    if not isinstance(enabled, bool):
        return envelope(error={'code': 'invalid', 'message': '`enabled` must be a boolean', 'details': None}, status=422)

    current = file_settings.get('enquiry_forwarding')
    new_value = {'enabled': enabled}
    file_settings.set('enquiry_forwarding', new_value)

    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_update_enquiry_forwarding',
        entity='site_setting',
        entity_id='enquiry_forwarding',
        diff={'old': current, 'new': new_value},
        ip=request.remote_addr,
    )
    return envelope(data=new_value, status=200)


# ── Admin: AI (Gemini) API key ─────────────────────────────────────────────────
# Read-only -- the key itself only ever lives in the AI_API_KEY env var, set
# directly on the server. There's no admin form to set/replace/clear it.

@blp.route('/admin/settings/ai', methods=['GET'])
@roles_required('admin')
def admin_get_ai_settings():
    return envelope(data={'configured': ai_config_service.is_configured()}, status=200)


# ── Admin: chatbot health dashboard ─────────────────────────────────────────────
# "Configured" only means a key is present, not that it currently works (wrong
# key, expired billing, zero quota all still count as configured) -- this
# combines that with the outcome of the most recent *real* Gemini call
# (ai_config_service.last_status, updated by chat/service.py) plus DB
# connectivity and cache/traffic stats into one dashboard read.

@blp.route('/admin/health/chatbot', methods=['GET'])
@roles_required('admin')
def admin_chatbot_health():
    try:
        db.session.execute(text('SELECT 1'))
        db_connected = True
    except Exception:
        db_connected = False

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    return envelope(data={
        'db_connected': db_connected,
        'chatbot_widget_enabled': file_settings.get('chatbot').get('enabled', False),
        'ai_configured': ai_config_service.is_configured(),
        'ai_last_status': ai_config_service.last_status(),
        'cache_entries': ChatQaCache.query.count(),
        'cache_total_hits': int(db.session.query(func.coalesce(func.sum(ChatQaCache.hit_count), 0)).scalar()),
        'sessions_today': ChatSession.query.filter(ChatSession.started_at >= today_start).count(),
    }, status=200)
