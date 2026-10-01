from __future__ import annotations

from flask import Blueprint, g, request

from app.services import file_settings
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


# ── Enquiry forwarding on/off — same flat-file approach as season ───────────
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
