from __future__ import annotations

import json
import queue as _queue

from flask import Blueprint, Response, g, request, stream_with_context

from app.extensions import db
from app.models.site_setting import SiteSetting
from app.services.audit_service import log_audit_action
from app.services.sse_broadcast import broadcast, subscribe, unsubscribe
from app.utils.decorators import roles_required
from app.utils.response import envelope

blp = Blueprint('settings', __name__)

_SEASON_KEY = 'season'
_DEFAULT_SEASON: dict = {'enabled': True, 'override': 'winter'}
_VALID_OVERRIDES = {'auto', 'winter', 'spring', 'summer', 'autumn', 'off'}


def _current_season() -> dict:
    return SiteSetting.get(_SEASON_KEY, _DEFAULT_SEASON)


# ── Public: read current season ───────────────────────────────────────────────

@blp.route('/settings/season')
def get_season():
    return envelope(data=_current_season(), status=200)


# ── Public: SSE stream — real-time season updates ─────────────────────────────

@blp.route('/settings/season/stream')
def season_stream():
    q = subscribe()
    initial = json.dumps(_current_season())

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
