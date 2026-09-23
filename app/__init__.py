from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

from flask import Flask, g, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import config_by_name
from .errors import register_error_handlers
from .extensions import api, cors, db, jwt, limiter, migrate
from .cli import register_cli
from .logging import configure_logging
from .services import monitoring
from .utils.response import error_envelope

_REQUEST_ID_RE = re.compile(r'^[a-zA-Z0-9\-]{8,36}$')
_SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def create_app() -> Flask:
    configure_logging()
    app = Flask(__name__)

    env = os.getenv('APP_ENV', 'development').lower()
    config_class = config_by_name.get(env, config_by_name['development'])
    app.config.from_object(config_class)
    app.config['APP_ENV'] = env

    if env == 'production':
        # Fail fast if required secrets are missing or insecure
        config_class.validate()
        # Trust 1 proxy hop so rate limiting uses real client IP
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # First, so its timers wrap every other hook -- including requests the
    # rate limiter rejects.
    monitoring.init_app(app)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    cors.init_app(
        app,
        resources={r'/*': {'origins': app.config['CORS_ORIGINS']}},
        supports_credentials=True,
    )
    limiter.init_app(app)
    api.init_app(app)
    register_error_handlers(app)
    register_cli(app)

    @app.before_request
    def _bind_request_context():
        raw_id = request.headers.get('X-Request-Id', '')
        g.request_id = raw_id if _REQUEST_ID_RE.match(raw_id) else str(uuid.uuid4())
        g.started_at = datetime.now(timezone.utc)

    @app.before_request
    def _require_csrf_header_for_admin_writes():
        # The admin JWT cookie is SameSite=None (see project_settings.py), so a
        # third-party page could submit a plain HTML form -- including the
        # multipart file uploads, which need no JSON body -- and the browser
        # would attach the cookie. A custom header can't be added by a plain
        # form, and adding one from script forces a CORS preflight that the
        # origin allowlist rejects. Bearer-token callers aren't exposed to
        # CSRF, so they're exempt.
        if request.method in _SAFE_METHODS or not request.path.startswith('/admin'):
            return None
        if request.headers.get('Authorization'):
            return None
        if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
            return error_envelope('csrf_failed', 'Missing X-Requested-With header', None, 403)
        return None

    @app.after_request
    def _attach_request_headers(response):
        response.headers['X-Request-Id'] = getattr(g, 'request_id', '')
        # Security headers
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '0'  # disabled — modern browsers handle this; value=1 can introduce vulnerabilities
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=(), payment=()'
        response.headers['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'none'"
        # Default to no-store, but keep an explicit policy set by the view
        # (public images under /media, the email logo, the admin photo) --
        # overwriting those made browsers re-download and the server
        # re-decrypt every image on every page view.
        if 'Cache-Control' not in response.headers:
            response.headers['Cache-Control'] = 'no-store'
        # Remove server fingerprinting headers
        response.headers.pop('Server', None)
        response.headers.pop('X-Powered-By', None)
        if env == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=63072000; includeSubDomains; preload'
        return response

    @jwt.user_identity_loader
    def _identity_loader(user):
        return user.id if hasattr(user, 'id') else user

    @jwt.token_in_blocklist_loader
    def _check_blocklist(_jwt_header, jwt_data):
        from app.models.token_blacklist import TokenBlacklist
        jti = jwt_data.get('jti')
        if not jti:
            return False
        return TokenBlacklist.is_revoked(jti)

    from app.blueprints.analytics.routes import blp as analytics_blp
    from app.blueprints.media.routes import blp as media_blp
    from app.blueprints.chat.routes import blp as chat_blp
    from app.blueprints.public.contact_routes import blp as contact_blp
    from app.blueprints.public.quote_routes import blp as quote_blp
    from app.blueprints.public.job_routes import blp as job_blp
    from app.blueprints.public.job_posting_routes import blp as job_posting_blp
    from app.blueprints.public.remote_support_routes import blp as remote_support_blp
    from app.blueprints.public.blog_routes import blp as blog_blp
    from app.blueprints.public.sitemap_routes import blp as sitemap_blp
    from app.blueprints.admin.routes import blp as admin_blp
    from app.blueprints.health.routes import blp as health_blp
    from app.blueprints.settings.routes import blp as settings_blp
    from app.blueprints.ops.routes import blp as ops_blp

    api.register_blueprint(analytics_blp)
    api.register_blueprint(chat_blp)
    api.register_blueprint(contact_blp)
    api.register_blueprint(quote_blp)
    api.register_blueprint(job_blp)
    api.register_blueprint(job_posting_blp)
    api.register_blueprint(remote_support_blp)
    api.register_blueprint(blog_blp)
    api.register_blueprint(sitemap_blp)
    api.register_blueprint(admin_blp)
    app.register_blueprint(health_blp)
    app.register_blueprint(media_blp)
    app.register_blueprint(settings_blp)
    app.register_blueprint(ops_blp)

    return app
