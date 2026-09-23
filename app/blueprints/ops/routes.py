from __future__ import annotations

from flask import Blueprint, current_app, request

from app.services import background, monitoring
from app.utils.decorators import roles_required
from app.utils.response import envelope

blp = Blueprint('ops', __name__)


@blp.route('/admin/monitoring', methods=['GET'])
@roles_required('admin')
def admin_monitoring():
    """Traffic, errors, latency, worker-process memory and server vitals for
    Admin -> Monitoring. `hours` is the traffic window (1-168)."""
    hours = min(max(request.args.get('hours', 24, type=int), 1), 168)
    cfg = current_app.config
    paths = [p for p in (cfg.get('MEDIA_UPLOAD_DIR'), cfg.get('COMPANY_FILES_DIR')) if p]
    return envelope(data={
        'traffic': monitoring.summary(hours),
        'processes': monitoring.live_processes(),
        'system': monitoring.system_vitals(paths),
        'this_process_background_pending': background.pending_count(),
        'thresholds': {
            'slow_request_ms': cfg['SLOW_REQUEST_MS'],
            'memory_warn_mb': cfg['MEMORY_WARN_MB'],
        },
    }, status=200)
