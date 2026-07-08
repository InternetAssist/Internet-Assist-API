from __future__ import annotations

import logging
from pathlib import Path

from flask import request

log = logging.getLogger(__name__)
from flask_smorest import Blueprint
from werkzeug.utils import secure_filename

_ALLOWED_CV_EXTENSIONS = {'.pdf', '.doc', '.docx'}

from app.extensions import db, limiter
from app.models.job_application import JobApplication
from app.schemas.public import JobApplicationFormSchema
from app.services.audit_service import log_audit_action
from app.services.email_service import send_ticket_with_attachments, send_confirmation
from app.services.media_service import save_document, load_document
from app.utils.response import envelope

blp = Blueprint('public-jobs', __name__, description='Job applications')


@blp.route('/job-applications', methods=['POST'])
@blp.arguments(JobApplicationFormSchema, location='form')
@limiter.limit('5/minute')
def create_job(payload):
    upload = request.files.get('cv')
    cv_file_name = None
    cv_original_name = None
    if upload and upload.filename:
        ext = Path(secure_filename(upload.filename)).suffix.lower()
        if ext not in _ALLOWED_CV_EXTENSIONS:
            from app.utils.response import error_envelope
            return error_envelope('invalid_file', 'CV must be a PDF, DOC, or DOCX file.', None, 422)
        cv_original_name = secure_filename(upload.filename)
        try:
            cv_file_name = save_document(upload.read(), ext)
        except Exception as exc:
            log.error('CV save failed for applicant %s: %s', payload.get('email'), exc, exc_info=True)
            cv_file_name = None

    application = JobApplication(
        full_name=payload['full_name'],
        email=payload['email'].lower(),
        phone=payload.get('phone'),
        position=payload['position'],
        cover_letter=payload.get('cover_letter'),
        cv_blob_url=cv_file_name,
        status='new',
    )
    db.session.add(application)
    db.session.commit()

    fields = {
        'Name':         application.full_name,
        'Email':        application.email,
        'Phone':        application.phone,
        'Position':     application.position,
        'Cover Letter': application.cover_letter,
        'CV':           cv_original_name or 'Not provided',
    }

    # Notify HR via internal email with CV attached (best-effort)
    try:
        email_attachments = None
        if cv_file_name and cv_original_name:
            doc = load_document(cv_file_name)
            if doc:
                email_attachments = [(doc[0], cv_original_name)]
        send_ticket_with_attachments(
            ticket_type='job_application',
            ticket_id=application.id,
            fields=fields,
            user_email=application.email,
            attachments=email_attachments,
        )
    except Exception:
        pass

    # Confirmation email to applicant
    try:
        send_confirmation(
            ticket_type='job_application',
            recipient_email=application.email,
            recipient_name=application.full_name,
            ticket_ref=None,
            details=fields,
        )
    except Exception:
        pass

    try:
        log_audit_action(action='public_job_created', entity='job_application', entity_id=application.id, ip=request.remote_addr)
    except Exception:
        pass
    return envelope(data={'id': application.id, 'status': application.status}, status=201)
