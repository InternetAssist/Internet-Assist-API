from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from pathlib import Path


class BaseConfig:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-only-insecure-key-change-in-production')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # pool_recycle stays under MariaDB's default wait_timeout so the server
    # never hands back a connection it has already closed. The pool must be at
    # least as big as the WSGI thread count (WAITRESS_THREADS, default 8) or
    # requests queue for a connection under load.
    _db_url = os.getenv('DATABASE_URL', '')
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
        **({} if _db_url.startswith('sqlite') else {
            'pool_size':    int(os.getenv('DB_POOL_SIZE', '10')),
            'max_overflow': int(os.getenv('DB_MAX_OVERFLOW', '10')),
            'pool_timeout': 10,
        }),
    }
    SQLALCHEMY_RECORD_QUERIES = False

    API_TITLE = 'Internet Assist API'
    API_VERSION = 'v1'
    API_PREFIX = '/api/v1'
    OPENAPI_VERSION = '3.0.3'
    OPENAPI_URL_PREFIX = '/'
    OPENAPI_SWAGGER_UI_PATH = '/docs'
    OPENAPI_SWAGGER_UI_URL = 'https://cdn.jsdelivr.net/npm/swagger-ui-dist/'

    JWT_PRIVATE_KEY = os.getenv('JWT_PRIVATE_KEY', '')
    JWT_PUBLIC_KEY = os.getenv('JWT_PUBLIC_KEY', '')
    JWT_ALGORITHM = 'RS256' if JWT_PRIVATE_KEY and JWT_PUBLIC_KEY else 'HS256'
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', SECRET_KEY)
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=8)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=7)

    # Cookie-based JWT settings (httpOnly). The frontend is also served from
    # ia-webdesign.com, a genuinely different registrable domain from
    # api.ia.uk (not a subdomain of ia.uk) -- SameSite=Lax silently drops the
    # cookie on cross-site fetch() calls from there, so production needs
    # SameSite=None (requires Secure, already forced below). CSRF is instead
    # covered by the CORS origin allowlist + a required X-Requested-With
    # header on every non-GET /admin request (app/__init__.py) -- a plain
    # cross-site HTML form can't set it. flask-jwt-extended's double-submit
    # CSRF can't work here: the frontend can't read a cookie set on api.ia.uk.
    _is_production = os.getenv('APP_ENV', 'development') == 'production'
    JWT_TOKEN_LOCATION = ['headers', 'cookies']
    JWT_COOKIE_SECURE = _is_production
    JWT_COOKIE_SAMESITE = 'None' if _is_production else 'Lax'
    JWT_COOKIE_CSRF_PROTECT = False  # see the X-Requested-With check in app/__init__.py
    JWT_ACCESS_COOKIE_NAME = 'access_token'

    # Rate limiting — uses in-memory store by default; set REDIS_URL for multi-worker
    RATELIMIT_STORAGE_URI = os.getenv('REDIS_URL', 'memory://')

    # Dev-only fallback so a fresh clone works without an .env — production
    # must set CORS_ORIGINS explicitly (enforced by ProductionConfig.validate).
    _default_cors_origins = '' if _is_production else 'http://localhost:5173,https://internet-assist.vercel.app'
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv('CORS_ORIGINS', _default_cors_origins).split(',')
        if origin.strip()
    ]

    # 10 MB for every route by default -- this also caps the public CV upload
    # and JSON endpoints, which are read fully into memory. The admin company
    # installer upload (NinjaOne MSIs etc, often 50MB+) raises its own limit
    # per-request instead -- see upload_company_file in admin/routes.py.
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    JSON_SORT_KEYS = False
    APPINSIGHTS_CONNECTION_STRING = os.getenv('APPINSIGHTS_CONNECTION_STRING', '')

    GRAPH_TENANT_ID     = os.getenv('GRAPH_TENANT_ID', '')
    GRAPH_CLIENT_ID     = os.getenv('GRAPH_CLIENT_ID', '')
    GRAPH_CLIENT_SECRET = os.getenv('GRAPH_CLIENT_SECRET', '')
    GRAPH_SENDER        = os.getenv('GRAPH_SENDER', '')
    NOTIFY_EMAIL_1      = os.getenv('NOTIFY_EMAIL_1', '')
    NOTIFY_EMAIL_2      = os.getenv('NOTIFY_EMAIL_2', '')

    AI_PROVIDER   = os.getenv('AI_PROVIDER', 'gemini')
    AI_MODEL_NAME = os.getenv('AI_MODEL_NAME', 'gemini-flash-latest')
    AI_API_KEY    = os.getenv('AI_API_KEY', '')

    # reCAPTCHA v3 -- second layer of bot protection on public forms, on top
    # of the honeypot field. Blank = verification is skipped gracefully
    # (lets local dev/testing work without needing real Google API keys).
    RECAPTCHA_SECRET_KEY = os.getenv('RECAPTCHA_SECRET_KEY', '')

    # "Sign in with Microsoft" admin login — reuses the same Azure AD app
    # registration as GRAPH_* above (already used for sending email) unless
    # overridden. Access is gated on holding the ia-support-admin App Role on
    # that app's service principal — see app/services/ms_auth_service.py.
    MS_AUTH_TENANT_ID     = os.getenv('MS_AUTH_TENANT_ID', GRAPH_TENANT_ID)
    MS_AUTH_CLIENT_ID     = os.getenv('MS_AUTH_CLIENT_ID', GRAPH_CLIENT_ID)
    MS_AUTH_CLIENT_SECRET = os.getenv('MS_AUTH_CLIENT_SECRET', GRAPH_CLIENT_SECRET)
    MS_AUTH_SP_ID         = os.getenv('MS_AUTH_SP_ID', 'cdac666d-40b4-4103-bb24-094e6aee55c7')
    MS_AUTH_ADMIN_ROLE_ID = os.getenv('MS_AUTH_ADMIN_ROLE_ID', '0b6bc22f-8ead-4b2e-8392-6ab2e8a003d3')
    MS_AUTH_AUTHORITY     = f'https://login.microsoftonline.com/{MS_AUTH_TENANT_ID}'

    # Session cookie carries the msal OAuth flow's state/nonce/PKCE verifier
    # across the redirect to Microsoft and back — Lax (not Strict) is required
    # for the cookie to survive that top-level cross-site redirect.
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Dev-only fallback — production must set FRONTEND_URL explicitly
    # (enforced by ProductionConfig.validate).
    FRONTEND_URL         = os.getenv('FRONTEND_URL', '' if _is_production else 'http://localhost:8081')
    UPLOAD_FOLDER        = str(Path('/tmp') / 'internet-assist-uploads')
    MEDIA_UPLOAD_DIR     = os.getenv('MEDIA_UPLOAD_DIR', str(Path(tempfile.gettempdir()) / 'ia-media'))
    # Company installer files (NinjaOne MSIs etc) can be much larger than
    # images/CVs -- kept in their own configurable location so it can point
    # at a different disk/volume in production. Defaults to a subfolder of
    # MEDIA_UPLOAD_DIR if not set explicitly.
    COMPANY_FILES_DIR    = os.getenv('COMPANY_FILES_DIR', '')
    MEDIA_ENCRYPTION_KEY = os.getenv('MEDIA_ENCRYPTION_KEY', '')
    SITE_SETTINGS_DIR    = os.getenv('SITE_SETTINGS_DIR', str(Path(tempfile.gettempdir()) / 'ia-site-settings'))
    PUBLIC_CONTACT_EMAIL = os.getenv('PUBLIC_CONTACT_EMAIL', 'enquiries@ia.uk')
    PUBLIC_CONTACT_PHONE = os.getenv('PUBLIC_CONTACT_PHONE', '01621 840014')
    TICKET_API_URL       = os.getenv('TICKET_API_URL', '')

    # Emails and other slow side-effects run on a small, bounded thread pool
    # so a visitor's request doesn't wait on Microsoft Graph.
    BACKGROUND_WORKERS = int(os.getenv('BACKGROUND_WORKERS', '2'))

    # ── Chatbot ──────────────────────────────────────────────────────────────
    # Public website the chatbot learns from (sitemap.xml + prerendered pages).
    SITE_URL = os.getenv('SITE_URL', 'https://www.ia.uk').rstrip('/')
    CHAT_KNOWLEDGE_MAX_AGE_HOURS  = int(os.getenv('CHAT_KNOWLEDGE_MAX_AGE_HOURS', '24'))
    # Hard caps on Gemini spend. Off-topic questions and greetings never reach
    # the AI at all; these bound what's left.
    CHAT_MAX_AI_CALLS_PER_SESSION = int(os.getenv('CHAT_MAX_AI_CALLS_PER_SESSION', '15'))
    CHAT_MAX_AI_CALLS_PER_DAY     = int(os.getenv('CHAT_MAX_AI_CALLS_PER_DAY', '500'))
    # Meaning-based matching (app/services/embedding_service.py). Uses
    # AI_API_KEY; without it, or while the API is failing, the chatbot falls
    # back to keyword search automatically.
    EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL', 'gemini-embedding-2')
    EMBEDDING_DIM   = int(os.getenv('EMBEDDING_DIM', '768'))

    # ── Monitoring ───────────────────────────────────────────────────────────
    MONITORING_ENABLED     = os.getenv('MONITORING_ENABLED', 'true').lower() == 'true'
    SLOW_REQUEST_MS        = int(os.getenv('SLOW_REQUEST_MS', '2000'))
    MEMORY_WARN_MB         = int(os.getenv('MEMORY_WARN_MB', '700'))
    # Retention for the automatic daily clean-up (app/services/maintenance.py).
    RETENTION_PAGE_VIEWS_DAYS     = int(os.getenv('RETENTION_PAGE_VIEWS_DAYS', '395'))
    RETENTION_CHAT_DAYS           = int(os.getenv('RETENTION_CHAT_DAYS', '90'))
    RETENTION_AUDIT_DAYS          = int(os.getenv('RETENTION_AUDIT_DAYS', '730'))
    RETENTION_METRICS_DAYS        = int(os.getenv('RETENTION_METRICS_DAYS', '30'))

    @staticmethod
    def database_url() -> str:
        url = os.getenv('DATABASE_URL')
        if not url:
            raise RuntimeError('DATABASE_URL env var is required but not set')
        return url


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = BaseConfig.database_url()


class TestingConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///:memory:')
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=5)
    RATELIMIT_STORAGE_URI = 'memory://'
    WTF_CSRF_ENABLED = False
    MONITORING_ENABLED = False


class ProductionConfig(BaseConfig):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = BaseConfig.database_url()
    SESSION_COOKIE_SECURE   = True
    SESSION_COOKIE_HTTPONLY = True
    # Disable Swagger UI in production — docs endpoint must not be publicly reachable
    OPENAPI_SWAGGER_UI_PATH = None

    @classmethod
    def validate(cls) -> None:
        """Fail fast if required production secrets are missing or still set to defaults."""
        insecure_key = 'dev-only-insecure-key-change-in-production'
        if not os.getenv('SECRET_KEY') or os.getenv('SECRET_KEY') == insecure_key:
            raise RuntimeError('SECRET_KEY must be set to a strong random value in production')
        if not os.getenv('JWT_SECRET_KEY'):
            raise RuntimeError('JWT_SECRET_KEY must be set in production')
        if not os.getenv('MEDIA_ENCRYPTION_KEY'):
            raise RuntimeError('MEDIA_ENCRYPTION_KEY must be set in production')
        if not os.getenv('DATABASE_URL'):
            raise RuntimeError('DATABASE_URL must be set in production')
        if not os.getenv('CORS_ORIGINS'):
            raise RuntimeError('CORS_ORIGINS must be set in production')
        if not os.getenv('FRONTEND_URL'):
            raise RuntimeError('FRONTEND_URL must be set in production')
        if 'localhost' in os.getenv('CORS_ORIGINS', '') or 'localhost' in os.getenv('FRONTEND_URL', ''):
            raise RuntimeError('CORS_ORIGINS/FRONTEND_URL must not contain localhost in production')


config_by_name = {
    'development': DevelopmentConfig,
    'testing':     TestingConfig,
    'production':  ProductionConfig,
}
