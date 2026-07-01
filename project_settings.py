from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from pathlib import Path


class BaseConfig:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-only-insecure-key-change-in-production')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True}
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

    # Cookie-based JWT settings (httpOnly, SameSite=Lax by default)
    JWT_TOKEN_LOCATION = ['headers', 'cookies']
    JWT_COOKIE_SECURE = os.getenv('APP_ENV', 'development') == 'production'
    JWT_COOKIE_SAMESITE = 'Lax'
    JWT_COOKIE_CSRF_PROTECT = False  # CORS allowlist + SameSite=Lax provides CSRF protection
    JWT_ACCESS_COOKIE_NAME = 'access_token'

    # Rate limiting — uses in-memory store by default; set REDIS_URL for multi-worker
    RATELIMIT_STORAGE_URI = os.getenv('REDIS_URL', 'memory://')

    _default_cors_origins = 'http://localhost:5173,https://internet-assist.vercel.app'
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv('CORS_ORIGINS', _default_cors_origins).split(',')
        if origin.strip()
    ]

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB upload cap
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

    INITIAL_ADMIN_EMAIL    = os.getenv('INITIAL_ADMIN_EMAIL', 'admin@internetassist.co.uk')
    INITIAL_ADMIN_PASSWORD = os.getenv('INITIAL_ADMIN_PASSWORD', 'ChangeMe123!')
    AUTO_SEED_ADMIN        = os.getenv('AUTO_SEED_ADMIN', 'false').lower() == 'true'

    FRONTEND_URL         = os.getenv('FRONTEND_URL', 'http://localhost:8081')
    UPLOAD_FOLDER        = str(Path('/tmp') / 'internet-assist-uploads')
    MEDIA_UPLOAD_DIR     = os.getenv('MEDIA_UPLOAD_DIR', str(Path(tempfile.gettempdir()) / 'ia-media'))
    MEDIA_ENCRYPTION_KEY = os.getenv('MEDIA_ENCRYPTION_KEY', '')
    PUBLIC_CONTACT_EMAIL = os.getenv('PUBLIC_CONTACT_EMAIL', 'enquiries@internetassist.co.uk')
    PUBLIC_CONTACT_PHONE = os.getenv('PUBLIC_CONTACT_PHONE', '01621 840014')
    TICKET_API_URL       = os.getenv('TICKET_API_URL', '')

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


class ProductionConfig(BaseConfig):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = BaseConfig.database_url()
    SESSION_COOKIE_SECURE   = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
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


config_by_name = {
    'development': DevelopmentConfig,
    'testing':     TestingConfig,
    'production':  ProductionConfig,
}
