from __future__ import annotations
from marshmallow import ValidationError
from werkzeug.exceptions import HTTPException
from .utils.response import error_envelope
def register_error_handlers(app):
    @app.errorhandler(ValidationError)
    def handle_validation_error(err):
        return error_envelope('validation_error', 'Validation failed', err.messages, 422)
    @app.errorhandler(HTTPException)
    def handle_http_exception(err):
        # flask-smorest stores marshmallow errors in err.data['messages'] on 422
        details = None
        if hasattr(err, 'data') and isinstance(err.data, dict):
            details = err.data.get('messages') or err.data.get('errors')
        description = err.description or err.name
        if err.code == 422 and details:
            description = 'Validation failed'
        if err.code == 413:
            description = 'The upload is too large. Files must be under 10 MB.'
        return error_envelope('http_error', description, details, err.code or 500)
    @app.errorhandler(Exception)
    def handle_generic_error(err):
        app.logger.exception('Unhandled error')
        return error_envelope('internal_error', 'Internal server error', None, 500)
