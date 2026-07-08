from __future__ import annotations

from datetime import datetime, timezone

from flask import abort, current_app, g, make_response, redirect, request, session, url_for
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity, jwt_required, set_access_cookies, unset_jwt_cookies
from flask_smorest import Blueprint
from sqlalchemy import func, or_

from app.extensions import db, jwt, limiter
from app.models.token_blacklist import TokenBlacklist
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.company_file import CompanyFile
from app.models.contact import Contact
from app.models.job_application import JobApplication
from app.models.job_posting import JobPosting
from app.models.project import Project
from app.models.quote import Quote
from app.schemas.admin import (
    CompanyCreateSchema, CompanyFilePatchSchema, CompanyFileUploadSchema, CompanyPatchSchema,
    JobPostingCreateSchema,
    PaginationQuerySchema, PatchStatusSchema, ProjectCreateSchema, ProjectPatchSchema,
)
from app.services.audit_service import log_audit_action
from app.services.email_service import send_job_status_update
from app.services.media_service import (
    delete_document, delete_image,
    load_company_file, load_document,
    save_company_file, save_image,
)
from app.services.ms_auth_service import (
    build_auth_flow, complete_auth_flow, get_profile_details, get_profile_photo, has_admin_app_role,
)
from app.utils.decorators import roles_required
from app.utils.response import envelope

blp = Blueprint('admin', __name__, description='Admin APIs')


def _paginate(query, page: int, page_size: int):
    total = query.order_by(None).count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return items, {'page': page, 'page_size': page_size, 'total': total, 'has_more': page * page_size < total}


# ── Auth: Sign in with Microsoft ────────────────────────────────────────────
# Authentication (who is this person) happens via an interactive OAuth flow
# against Azure AD. Authorization (do they get in) is a separate check against
# the ia-support-admin App Role on the same app registration, using the
# app-only Graph credentials already used for sending email — see
# app/services/ms_auth_service.py. There's no local users table — Azure is
# the source of truth for identity and role. Everything the app needs about
# who's signed in (email, name, roles) is baked into the JWT itself at login
# time and read back out by roles_required() (app/utils/decorators.py) on
# every request — see CurrentUser there.

@blp.route('/admin/login/microsoft', methods=['GET'])
@limiter.limit('10/minute')
def login_microsoft():
    redirect_uri = url_for('admin.microsoft_callback', _external=True)
    flow = build_auth_flow(redirect_uri)
    session['ms_flow'] = flow
    return redirect(flow['auth_uri'])


@blp.route('/admin/login/microsoft/callback', methods=['GET'])
@limiter.limit('10/minute')
def microsoft_callback():
    frontend_url = current_app.config.get('FRONTEND_URL', 'http://localhost:8081')
    flow = session.pop('ms_flow', None)
    if not flow:
        return redirect(f'{frontend_url}/auth?error=session_expired')

    result = complete_auth_flow(flow, request.args)
    if 'error' in result:
        log_audit_action(
            actor_user_id=None,
            action='admin_login_failed',
            entity='user',
            diff={'error': result.get('error')},
            ip=request.remote_addr,
        )
        return redirect(f'{frontend_url}/auth?error=login_failed')

    claims = result.get('id_token_claims', {})
    oid = claims.get('oid')
    email = (claims.get('email') or claims.get('preferred_username') or '').lower()
    full_name = claims.get('name') or email

    if not oid or not email:
        return redirect(f'{frontend_url}/auth?error=login_failed')

    try:
        authorized = has_admin_app_role(oid)
    except Exception:
        current_app.logger.exception('ms_role_check_failed')
        return redirect(f'{frontend_url}/auth?error=role_check_failed')

    if not authorized:
        log_audit_action(
            actor_user_id=None,
            action='admin_login_denied_no_role',
            entity='user',
            diff={'email': email, 'oid': oid},
            ip=request.remote_addr,
        )
        return redirect(f'{frontend_url}/auth?error=forbidden')

    try:
        profile = get_profile_details(oid)
    except Exception:
        current_app.logger.exception('ms_profile_fetch_failed')
        profile = {'job_title': None, 'department': None}

    token = create_access_token(
        identity=email,
        additional_claims={
            'full_name': full_name,
            'roles': ['admin'],
            'oid': oid,
            'job_title': profile.get('job_title'),
            'department': profile.get('department'),
        },
    )
    log_audit_action(
        actor_user_id=email,
        action='admin_login_success',
        entity='user',
        entity_id=email,
        ip=request.remote_addr,
    )
    resp = make_response(redirect(f'{frontend_url}/admin'))
    set_access_cookies(resp, token)
    return resp


@blp.route('/admin/me', methods=['GET'])
@jwt_required()
def admin_me():
    email = get_jwt_identity()
    claims = get_jwt()
    return envelope(data={
        'id': email,
        'email': email,
        'full_name': claims.get('full_name', email),
        'roles': claims.get('roles', []),
        'job_title': claims.get('job_title'),
        'department': claims.get('department'),
    }, status=200)


@blp.route('/admin/me/photo', methods=['GET'])
@jwt_required()
def admin_me_photo():
    oid = get_jwt().get('oid')
    if not oid:
        abort(404)
    try:
        result = get_profile_photo(oid)
    except Exception:
        current_app.logger.exception('ms_photo_fetch_failed')
        abort(502)
    if result is None:
        abort(404)
    photo_bytes, content_type = result
    resp = make_response(photo_bytes)
    resp.headers['Content-Type'] = content_type
    resp.headers['Cache-Control'] = 'private, max-age=3600'
    return resp


@blp.route('/admin/logout', methods=['POST'])
@jwt_required()
@limiter.limit('20/minute')
def admin_logout():
    jwt_data = get_jwt()
    jti = jwt_data.get('jti')
    exp = jwt_data.get('exp')
    if jti and exp:
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        if not TokenBlacklist.query.filter_by(jti=jti).first():
            db.session.add(TokenBlacklist(jti=jti, expires_at=expires_at))
            db.session.commit()
        TokenBlacklist.purge_expired()
    resp = make_response(envelope(data={'message': 'Logged out successfully.'}, status=200))
    unset_jwt_cookies(resp)
    return resp


# ── Serializers ───────────────────────────────────────────────────────────────

def _serialize_contact(item: Contact) -> dict:
    return {
        'id': item.id,
        'name': item.name,
        'email': item.email,
        'phone': item.phone,
        'company': item.company,
        'message': item.message,
        'status': item.status,
        'ticket_id': item.ticket_id,
        'ticket_ref': item.ticket_ref,
        'created_at': item.created_at.isoformat(),
    }


def _serialize_quote(item: Quote) -> dict:
    return {
        'id': item.id,
        'name': item.name,
        'email': item.email,
        'phone': item.phone,
        'company': item.company,
        'services': item.services,
        'team_size': item.team_size,
        'timeline': item.timeline,
        'details': item.details,
        'status': item.status,
        'ticket_id': item.ticket_id,
        'ticket_ref': item.ticket_ref,
        'created_at': item.created_at.isoformat(),
    }


def _serialize_job(item: JobApplication) -> dict:
    return {
        'id': item.id,
        'full_name': item.full_name,
        'email': item.email,
        'phone': item.phone,
        'position': item.position,
        'cover_letter': item.cover_letter,
        'has_cv': bool(item.cv_blob_url),
        'status': item.status,
        'created_at': item.created_at.isoformat(),
        'updated_at': item.updated_at.isoformat(),
    }


def _serialize_posting(item: JobPosting) -> dict:
    return {
        'id': item.id,
        'title': item.title,
        'team': item.team,
        'location': item.location,
        'type': item.employment_type,
        'summary': item.summary,
        'responsibilities': item.responsibilities or [],
        'requirements': item.requirements or [],
        'status': item.status,
        'created_at': item.created_at.isoformat(),
    }


# ── Contacts ─────────────────────────────────────────────────────────────────

@blp.route('/admin/contacts')
@roles_required('admin')
@blp.arguments(PaginationQuerySchema, location='query')
def list_contacts(payload):
    query = Contact.query
    if payload.get('status'):
        query = query.filter(Contact.status == payload['status'])
    else:
        query = query.filter(Contact.status != 'archived')
    if payload.get('q'):
        q = f"%{payload['q']}%"
        query = query.filter(or_(Contact.name.ilike(q), Contact.email.ilike(q), Contact.message.ilike(q)))
    items, meta = _paginate(query.order_by(Contact.created_at.desc()), payload['page'], payload['page_size'])
    log_audit_action(actor_user_id=g.current_user.id, action='admin_list_contacts', entity='contact', ip=request.remote_addr)
    return envelope(data=[_serialize_contact(i) for i in items], meta=meta, status=200)


@blp.route('/admin/contacts/<string:contact_id>', methods=['PATCH'])
@roles_required('admin')
@blp.arguments(PatchStatusSchema)
def patch_contact(payload, contact_id):
    contact = db.session.get(Contact, contact_id)
    if not contact:
        return envelope(error={'code': 'not_found', 'message': 'Contact not found', 'details': None}, status=404)
    old_status = contact.status
    contact.status = payload['status']
    db.session.commit()
    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_update_contact',
        entity='contact',
        entity_id=contact_id,
        diff={'status': {'old': old_status, 'new': payload['status']}},
        ip=request.remote_addr,
    )
    return envelope(data=_serialize_contact(contact), status=200)


@blp.route('/admin/contacts/<string:contact_id>', methods=['DELETE'])
@roles_required('admin')
def delete_contact(contact_id):
    contact = db.session.get(Contact, contact_id)
    if not contact:
        return envelope(error={'code': 'not_found', 'message': 'Contact not found', 'details': None}, status=404)
    old_status = contact.status
    contact.status = 'archived'
    db.session.commit()
    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_archive_contact',
        entity='contact',
        entity_id=contact_id,
        diff={'status': {'old': old_status, 'new': 'archived'}},
        ip=request.remote_addr,
    )
    return envelope(data={'id': contact_id}, status=200)


# ── Quotes ────────────────────────────────────────────────────────────────────

@blp.route('/admin/quotes')
@roles_required('admin')
@blp.arguments(PaginationQuerySchema, location='query')
def list_quotes(payload):
    query = Quote.query
    if payload.get('status'):
        query = query.filter(Quote.status == payload['status'])
    else:
        query = query.filter(Quote.status != 'archived')
    if payload.get('q'):
        q = f"%{payload['q']}%"
        query = query.filter(or_(Quote.name.ilike(q), Quote.email.ilike(q), Quote.details.ilike(q)))
    items, meta = _paginate(query.order_by(Quote.created_at.desc()), payload['page'], payload['page_size'])
    log_audit_action(actor_user_id=g.current_user.id, action='admin_list_quotes', entity='quote', ip=request.remote_addr)
    return envelope(data=[_serialize_quote(i) for i in items], meta=meta, status=200)


@blp.route('/admin/quotes/<string:quote_id>', methods=['PATCH'])
@roles_required('admin')
@blp.arguments(PatchStatusSchema)
def patch_quote(payload, quote_id):
    quote = db.session.get(Quote, quote_id)
    if not quote:
        return envelope(error={'code': 'not_found', 'message': 'Quote not found', 'details': None}, status=404)
    old_status = quote.status
    quote.status = payload['status']
    db.session.commit()
    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_update_quote',
        entity='quote',
        entity_id=quote_id,
        diff={'status': {'old': old_status, 'new': payload['status']}},
        ip=request.remote_addr,
    )
    return envelope(data=_serialize_quote(quote), status=200)


@blp.route('/admin/quotes/<string:quote_id>', methods=['DELETE'])
@roles_required('admin')
def delete_quote(quote_id):
    quote = db.session.get(Quote, quote_id)
    if not quote:
        return envelope(error={'code': 'not_found', 'message': 'Quote not found', 'details': None}, status=404)
    old_status = quote.status
    quote.status = 'archived'
    db.session.commit()
    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_archive_quote',
        entity='quote',
        entity_id=quote_id,
        diff={'status': {'old': old_status, 'new': 'archived'}},
        ip=request.remote_addr,
    )
    return envelope(data={'id': quote_id}, status=200)


# ── Job Applications ──────────────────────────────────────────────────────────

@blp.route('/admin/jobs')
@roles_required('admin')
@blp.arguments(PaginationQuerySchema, location='query')
def list_jobs(payload):
    query = JobApplication.query
    if payload.get('status'):
        query = query.filter(JobApplication.status == payload['status'])
    else:
        query = query.filter(JobApplication.status != 'archived')
    if payload.get('q'):
        q = f"%{payload['q']}%"
        query = query.filter(
            or_(JobApplication.full_name.ilike(q), JobApplication.email.ilike(q), JobApplication.position.ilike(q))
        )
    items, meta = _paginate(query.order_by(JobApplication.created_at.desc()), payload['page'], payload['page_size'])
    log_audit_action(actor_user_id=g.current_user.id, action='admin_list_jobs', entity='job_application', ip=request.remote_addr)
    return envelope(data=[_serialize_job(i) for i in items], meta=meta, status=200)


@blp.route('/admin/jobs/<string:job_id>', methods=['PATCH'])
@roles_required('admin')
@blp.arguments(PatchStatusSchema)
def patch_job(payload, job_id):
    job = db.session.get(JobApplication, job_id)
    if not job:
        return envelope(error={'code': 'not_found', 'message': 'Job application not found', 'details': None}, status=404)
    old_status = job.status
    new_status = payload['status']
    job.status = new_status
    db.session.commit()
    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_update_job',
        entity='job_application',
        entity_id=job_id,
        diff={'status': {'old': old_status, 'new': new_status}},
        ip=request.remote_addr,
    )
    if old_status != new_status:
        try:
            send_job_status_update(
                recipient_email=job.email,
                recipient_name=job.full_name,
                position=job.position,
                new_status=new_status,
            )
        except Exception:
            pass
    return envelope(data=_serialize_job(job), status=200)


@blp.route('/admin/jobs/<string:job_id>', methods=['GET'])
@roles_required('admin')
def get_job(job_id):
    job = db.session.get(JobApplication, job_id)
    if not job:
        return envelope(error={'code': 'not_found', 'message': 'Job application not found', 'details': None}, status=404)
    log_audit_action(actor_user_id=g.current_user.id, action='admin_get_job', entity='job_application', entity_id=job_id, ip=request.remote_addr)
    return envelope(data=_serialize_job(job), status=200)


@blp.route('/admin/jobs/<string:job_id>', methods=['DELETE'])
@roles_required('admin')
def delete_job(job_id):
    job = db.session.get(JobApplication, job_id)
    if not job:
        return envelope(error={'code': 'not_found', 'message': 'Job application not found', 'details': None}, status=404)
    old_status = job.status
    job.status = 'archived'
    db.session.commit()
    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_archive_job',
        entity='job_application',
        entity_id=job_id,
        diff={'status': {'old': old_status, 'new': 'archived'}},
        ip=request.remote_addr,
    )
    return envelope(data={'id': job_id}, status=200)


@blp.route('/admin/jobs/<string:job_id>/cv', methods=['GET'])
@roles_required('admin')
def download_job_cv(job_id):
    from pathlib import Path as _Path

    from flask import Response

    job = db.session.get(JobApplication, job_id)
    if not job:
        return envelope(error={'code': 'not_found', 'message': 'Job application not found', 'details': None}, status=404)
    if not job.cv_blob_url:
        return envelope(error={'code': 'no_cv', 'message': 'No CV on file for this application', 'details': None}, status=404)

    doc = load_document(job.cv_blob_url)
    if not doc:
        return envelope(error={'code': 'cv_unavailable', 'message': 'CV file could not be retrieved', 'details': None}, status=404)

    data, mime = doc
    ext = _Path(job.cv_blob_url).suffix
    safe_name = f"cv_{job.full_name.replace(' ', '_')}_{job_id[:8]}{ext}"
    log_audit_action(actor_user_id=g.current_user.id, action='admin_download_cv', entity='job_application', entity_id=job_id, ip=request.remote_addr)
    return Response(
        data,
        status=200,
        mimetype=mime,
        headers={'Content-Disposition': f'attachment; filename="{safe_name}"'},
    )


# ── Job Postings ──────────────────────────────────────────────────────────────

@blp.route('/admin/job-postings')
@roles_required('admin')
def admin_list_job_postings():
    postings = JobPosting.query.order_by(JobPosting.created_at.desc()).all()
    log_audit_action(actor_user_id=g.current_user.id, action='admin_list_job_postings', entity='job_posting', ip=request.remote_addr)
    return envelope(data=[_serialize_posting(p) for p in postings], status=200)


@blp.route('/admin/job-postings', methods=['POST'])
@roles_required('admin')
@blp.arguments(JobPostingCreateSchema)
def create_job_posting(payload):
    posting = JobPosting(
        title=payload['title'],
        team=payload.get('team'),
        location=payload.get('location'),
        employment_type=payload.get('type'),
        summary=payload.get('summary'),
        responsibilities=payload.get('responsibilities') or [],
        requirements=payload.get('requirements') or [],
    )
    db.session.add(posting)
    db.session.commit()
    log_audit_action(actor_user_id=g.current_user.id, action='admin_create_job_posting', entity='job_posting', entity_id=posting.id, ip=request.remote_addr)
    return envelope(data=_serialize_posting(posting), status=201)


@blp.route('/admin/job-postings/<string:posting_id>', methods=['PATCH'])
@roles_required('admin')
@blp.arguments(JobPostingCreateSchema(partial=True))
def patch_job_posting(payload, posting_id):
    posting = db.session.get(JobPosting, posting_id)
    if not posting:
        return envelope(error={'code': 'not_found', 'message': 'Job posting not found', 'details': None}, status=404)
    if 'title' in payload:
        posting.title = payload['title']
    if 'team' in payload:
        posting.team = payload.get('team')
    if 'location' in payload:
        posting.location = payload.get('location')
    if 'type' in payload:
        posting.employment_type = payload.get('type')
    if 'summary' in payload:
        posting.summary = payload.get('summary')
    if 'responsibilities' in payload:
        posting.responsibilities = payload.get('responsibilities') or []
    if 'requirements' in payload:
        posting.requirements = payload.get('requirements') or []
    if 'status' in payload:
        posting.status = payload['status']
    db.session.commit()
    log_audit_action(actor_user_id=g.current_user.id, action='admin_update_job_posting', entity='job_posting', entity_id=posting_id, ip=request.remote_addr)
    return envelope(data=_serialize_posting(posting), status=200)


@blp.route('/admin/job-postings/<string:posting_id>', methods=['DELETE'])
@roles_required('admin')
def delete_job_posting(posting_id):
    posting = db.session.get(JobPosting, posting_id)
    if not posting:
        return envelope(error={'code': 'not_found', 'message': 'Job posting not found', 'details': None}, status=404)
    db.session.delete(posting)
    db.session.commit()
    log_audit_action(actor_user_id=g.current_user.id, action='admin_delete_job_posting', entity='job_posting', entity_id=posting_id, ip=request.remote_addr)
    return envelope(data={'id': posting_id}, status=200)


# ── Stats ─────────────────────────────────────────────────────────────────────

@blp.route('/admin/stats')
@roles_required('admin')
def stats():
    contacts = {row[0]: row[1] for row in db.session.query(Contact.status, func.count(Contact.id)).group_by(Contact.status).all()}
    quotes = {row[0]: row[1] for row in db.session.query(Quote.status, func.count(Quote.id)).group_by(Quote.status).all()}
    jobs = {row[0]: row[1] for row in db.session.query(JobApplication.status, func.count(JobApplication.id)).group_by(JobApplication.status).all()}
    totals = {
        'contacts': Contact.query.count(),
        'quotes': Quote.query.count(),
        'jobs': JobApplication.query.count(),
        'postings': JobPosting.query.count(),
        'projects': Project.query.count(),
    }
    log_audit_action(actor_user_id=g.current_user.id, action='admin_view_stats', entity='stats', ip=request.remote_addr)
    return envelope(data={'contacts': contacts, 'quotes': quotes, 'jobs': jobs, 'totals': totals}, status=200)


# ── Audit Logs ────────────────────────────────────────────────────────────────

def _serialize_audit(log: AuditLog) -> dict:
    return {
        'id': log.id,
        'actor_user_id': log.actor_user_id,
        'action': log.action,
        'entity': log.entity,
        'entity_id': log.entity_id,
        'diff': log.diff,
        'ip': log.ip,
        'request_id': log.request_id,
        'created_at': log.created_at.isoformat(),
    }


@blp.route('/admin/audit-logs')
@roles_required('admin')
@blp.arguments(PaginationQuerySchema, location='query')
def list_audit_logs(payload):
    """List all audit logs with optional filtering by entity or action."""
    query = AuditLog.query
    if payload.get('status'):  # reuse 'status' query param as 'entity' filter
        query = query.filter(AuditLog.entity == payload['status'])
    if payload.get('q'):  # search in action or actor email
        q = f"%{payload['q']}%"
        query = query.filter(or_(AuditLog.action.ilike(q), AuditLog.actor_user_id.ilike(q)))
    items, meta = _paginate(query.order_by(AuditLog.created_at.desc()), payload['page'], payload['page_size'])
    log_audit_action(actor_user_id=g.current_user.id, action='admin_list_audit_logs', entity='audit_log', ip=request.remote_addr)
    return envelope(data=[_serialize_audit(i) for i in items], meta=meta, status=200)


@blp.route('/admin/audit-logs/<string:log_id>')
@roles_required('admin')
def get_audit_log(log_id):
    """Get a single audit log entry with full details."""
    log_entry = db.session.get(AuditLog, log_id)
    if not log_entry:
        return envelope(error={'code': 'not_found', 'message': 'Audit log not found', 'details': None}, status=404)
    log_audit_action(actor_user_id=g.current_user.id, action='admin_view_audit_log', entity='audit_log', entity_id=log_id, ip=request.remote_addr)
    return envelope(data=_serialize_audit(log_entry), status=200)


# ── Projects (Client Stories) ─────────────────────────────────────────────────

def _project_image_url(p: Project) -> str | None:
    if p.image_file_id:
        # IIS's HttpPlatformHandler proxies to waitress over plain HTTP and doesn't
        # set X-Forwarded-Proto, so request.host_url reports http even when the
        # public site is HTTPS-only. Force the scheme in production instead of
        # trusting proxy headers that aren't actually being sent.
        host = request.host_url.split('://', 1)[-1].rstrip('/')
        scheme = 'https' if current_app.config.get('APP_ENV') == 'production' else request.scheme
        return f"{scheme}://{host}/media/projects/{p.image_file_id}"
    return p.image_url or None


def _serialize_project(p: Project) -> dict:
    return {
        'id':            p.id,
        'title':         p.title,
        'client':        p.client,
        'industry':      p.industry,
        'summary':       p.summary,
        'challenge':     p.challenge,
        'solution':      p.solution,
        'outcome':       p.outcome,
        'tags':          p.tags or [],
        'service_type':  p.service_type,
        'image_url':     _project_image_url(p),
        'image_file_id': p.image_file_id,
        'project_url':   p.project_url,
        'status':        p.status,
        'created_at':    p.created_at.isoformat(),
        'updated_at':    p.updated_at.isoformat(),
    }


@blp.route('/admin/projects')
@roles_required('admin')
@blp.arguments(PaginationQuerySchema, location='query')
def list_projects(payload):
    query = Project.query
    if payload.get('status'):
        query = query.filter(Project.status == payload['status'])
    else:
        query = query.filter(Project.status != 'archived')
    if payload.get('q'):
        qstr = f"%{payload['q']}%"
        query = query.filter(or_(Project.title.ilike(qstr), Project.client.ilike(qstr), Project.summary.ilike(qstr)))
    items, meta = _paginate(query.order_by(Project.created_at.desc()), payload['page'], payload['page_size'])
    log_audit_action(actor_user_id=g.current_user.id, action='admin_list_projects', entity='project', ip=request.remote_addr)
    return envelope(data=[_serialize_project(p) for p in items], meta=meta, status=200)


@blp.route('/admin/projects', methods=['POST'])
@roles_required('admin')
@blp.arguments(ProjectCreateSchema)
def create_project(payload):
    proj = Project(
        title=payload['title'],
        client=payload.get('client'),
        industry=payload.get('industry'),
        summary=payload['summary'],
        challenge=payload.get('challenge'),
        solution=payload.get('solution'),
        outcome=payload.get('outcome'),
        tags=payload.get('tags') or [],
        service_type=payload['service_type'],
        image_url=payload.get('image_url'),
        project_url=payload.get('project_url'),
        status=payload.get('status') or 'draft',
    )
    db.session.add(proj)
    db.session.commit()
    log_audit_action(actor_user_id=g.current_user.id, action='admin_create_project', entity='project', entity_id=proj.id, ip=request.remote_addr)
    return envelope(data=_serialize_project(proj), status=201)


@blp.route('/admin/projects/<string:project_id>', methods=['PATCH'])
@roles_required('admin')
@blp.arguments(ProjectPatchSchema())
def patch_project(payload, project_id):
    proj = db.session.get(Project, project_id)
    if not proj:
        return envelope(error={'code': 'not_found', 'message': 'Project not found', 'details': None}, status=404)
    # Only update fields that were actually present in the request body.
    # ProjectPatchSchema uses load_default=None so every field appears in payload;
    # we use the raw JSON keys to tell which ones were explicitly sent.
    sent_keys = set((request.get_json(silent=True) or {}).keys())
    for field in ('title', 'client', 'industry', 'summary', 'challenge', 'solution', 'outcome', 'tags', 'service_type', 'image_url', 'project_url', 'status'):
        if field in sent_keys:
            setattr(proj, field, payload.get(field))
    db.session.commit()
    log_audit_action(actor_user_id=g.current_user.id, action='admin_update_project', entity='project', entity_id=project_id, ip=request.remote_addr)
    return envelope(data=_serialize_project(proj), status=200)


@blp.route('/admin/projects/<string:project_id>', methods=['DELETE'])
@roles_required('admin')
def delete_project(project_id):
    proj = db.session.get(Project, project_id)
    if not proj:
        return envelope(error={'code': 'not_found', 'message': 'Project not found', 'details': None}, status=404)
    old_status = proj.status
    proj.status = 'archived'
    db.session.commit()
    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_archive_project',
        entity='project',
        entity_id=project_id,
        diff={'status': {'old': old_status, 'new': 'archived'}},
        ip=request.remote_addr,
    )
    return envelope(data={'id': project_id}, status=200)


@blp.route('/admin/projects/<string:project_id>/image', methods=['POST'])
@roles_required('admin')
def upload_project_image(project_id):
    proj = db.session.get(Project, project_id)
    if not proj:
        return envelope(error={'code': 'not_found', 'message': 'Project not found', 'details': None}, status=404)

    upload = request.files.get('image')
    if not upload or not upload.filename:
        return envelope(error={'code': 'no_file', 'message': 'No image file provided.', 'details': None}, status=400)

    from pathlib import Path as _Path
    from werkzeug.utils import secure_filename
    ext = _Path(secure_filename(upload.filename)).suffix.lower()
    if ext not in {'.jpg', '.jpeg', '.png', '.webp', '.gif'}:
        return envelope(error={'code': 'invalid_type', 'message': 'Image must be JPG, PNG, WebP, or GIF.', 'details': None}, status=422)

    data = upload.read()
    if len(data) > 8 * 1024 * 1024:
        return envelope(error={'code': 'file_too_large', 'message': 'Image must be under 8 MB.', 'details': None}, status=413)

    # Remove old encrypted file if present
    if proj.image_file_id:
        delete_image(proj.image_file_id)

    try:
        file_name = save_image(data, ext)
    except Exception:
        return envelope(error={'code': 'upload_failed', 'message': 'Failed to store image.', 'details': None}, status=500)

    proj.image_file_id = file_name
    proj.image_url = None  # clear any external URL
    db.session.commit()

    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_upload_project_image',
        entity='project',
        entity_id=project_id,
        diff={'file': file_name},
        ip=request.remote_addr,
    )
    return envelope(data=_serialize_project(proj), status=200)


# ── Companies & their installer files (e.g. per-client NinjaOne MSIs) ─────────
# Files are encrypted at rest via media_service (same mechanism as CVs/project
# images) and only ever readable through these @roles_required('admin') routes
# — there is no public route for company files.

_ALLOWED_COMPANY_FILE_EXTENSIONS = {'.msi'}
_MAX_COMPANY_FILE_SIZE = 250 * 1024 * 1024  # matches MAX_CONTENT_LENGTH


def _serialize_company(c: Company) -> dict:
    return {
        'id': c.id,
        'name': c.name,
        'notes': c.notes,
        'status': c.status,
        'file_count': c.files.filter(CompanyFile.status != 'archived').count(),
        'created_at': c.created_at.isoformat(),
        'updated_at': c.updated_at.isoformat(),
    }


def _serialize_company_file(f: CompanyFile) -> dict:
    return {
        'id': f.id,
        'company_id': f.company_id,
        'original_filename': f.original_filename,
        'file_size': f.file_size,
        'description': f.description,
        'uploaded_by': f.uploaded_by,
        'status': f.status,
        'created_at': f.created_at.isoformat(),
    }


@blp.route('/admin/companies')
@roles_required('admin')
@blp.arguments(PaginationQuerySchema, location='query')
def list_companies(payload):
    query = Company.query
    if payload.get('status'):
        query = query.filter(Company.status == payload['status'])
    else:
        query = query.filter(Company.status != 'archived')
    if payload.get('q'):
        query = query.filter(Company.name.ilike(f"%{payload['q']}%"))
    items, meta = _paginate(query.order_by(Company.name.asc()), payload['page'], payload['page_size'])
    log_audit_action(actor_user_id=g.current_user.id, action='admin_list_companies', entity='company', ip=request.remote_addr)
    return envelope(data=[_serialize_company(c) for c in items], meta=meta, status=200)


@blp.route('/admin/companies', methods=['POST'])
@roles_required('admin')
@blp.arguments(CompanyCreateSchema)
def create_company(payload):
    if Company.query.filter(db.func.lower(Company.name) == payload['name'].lower()).first():
        return envelope(error={'code': 'duplicate', 'message': 'A company with this name already exists.', 'details': None}, status=409)

    company = Company(name=payload['name'], notes=payload.get('notes'))
    db.session.add(company)
    db.session.commit()

    log_audit_action(actor_user_id=g.current_user.id, action='admin_create_company', entity='company', entity_id=company.id, diff={'name': company.name}, ip=request.remote_addr)
    return envelope(data=_serialize_company(company), status=201)


@blp.route('/admin/companies/<string:company_id>', methods=['PATCH'])
@roles_required('admin')
@blp.arguments(CompanyPatchSchema)
def patch_company(payload, company_id):
    company = db.session.get(Company, company_id)
    if not company:
        return envelope(error={'code': 'not_found', 'message': 'Company not found', 'details': None}, status=404)

    diff = {}
    for field in ('name', 'notes'):
        if payload.get(field) is not None:
            diff[field] = {'old': getattr(company, field), 'new': payload[field]}
            setattr(company, field, payload[field])
    db.session.commit()

    log_audit_action(actor_user_id=g.current_user.id, action='admin_update_company', entity='company', entity_id=company_id, diff=diff, ip=request.remote_addr)
    return envelope(data=_serialize_company(company), status=200)


@blp.route('/admin/companies/<string:company_id>', methods=['DELETE'])
@roles_required('admin')
def delete_company(company_id):
    company = db.session.get(Company, company_id)
    if not company:
        return envelope(error={'code': 'not_found', 'message': 'Company not found', 'details': None}, status=404)

    # Soft delete — archive the company and its files, don't touch the
    # encrypted blobs on disk. Matches the archive pattern used everywhere
    # else in this admin panel (Contacts/Quotes/Jobs/Projects).
    old_status = company.status
    company.status = 'archived'
    company.files.update({CompanyFile.status: 'archived'}, synchronize_session=False)
    db.session.commit()

    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_archive_company',
        entity='company',
        entity_id=company_id,
        diff={'status': {'old': old_status, 'new': 'archived'}},
        ip=request.remote_addr,
    )
    return envelope(data={'id': company_id}, status=200)


@blp.route('/admin/companies/<string:company_id>/files')
@roles_required('admin')
def list_company_files(company_id):
    company = db.session.get(Company, company_id)
    if not company:
        return envelope(error={'code': 'not_found', 'message': 'Company not found', 'details': None}, status=404)

    files = company.files.filter(CompanyFile.status != 'archived').order_by(CompanyFile.created_at.desc()).all()
    log_audit_action(actor_user_id=g.current_user.id, action='admin_list_company_files', entity='company', entity_id=company_id, ip=request.remote_addr)
    return envelope(data=[_serialize_company_file(f) for f in files], status=200)


@blp.route('/admin/companies/<string:company_id>/files', methods=['POST'])
@roles_required('admin')
def upload_company_file(company_id):
    company = db.session.get(Company, company_id)
    if not company:
        return envelope(error={'code': 'not_found', 'message': 'Company not found', 'details': None}, status=404)

    upload = request.files.get('file')
    if not upload or not upload.filename:
        return envelope(error={'code': 'no_file', 'message': 'No file provided.', 'details': None}, status=400)

    from pathlib import Path as _Path
    from werkzeug.utils import secure_filename
    original_name = secure_filename(upload.filename)
    ext = _Path(original_name).suffix.lower()
    if ext not in _ALLOWED_COMPANY_FILE_EXTENSIONS:
        return envelope(error={'code': 'invalid_type', 'message': 'Only .msi files are accepted.', 'details': None}, status=422)

    data = upload.read()
    if len(data) > _MAX_COMPANY_FILE_SIZE:
        return envelope(error={'code': 'file_too_large', 'message': 'File exceeds the 250 MB limit.', 'details': None}, status=413)

    description = (request.form.get('description') or '').strip() or None

    try:
        stored_file_id = save_company_file(data, ext)
    except Exception:
        return envelope(error={'code': 'upload_failed', 'message': 'Failed to store file.', 'details': None}, status=500)

    company_file = CompanyFile(
        company_id=company_id,
        original_filename=original_name,
        stored_file_id=stored_file_id,
        file_size=len(data),
        description=description,
        uploaded_by=g.current_user.id,
    )
    db.session.add(company_file)
    db.session.commit()

    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_upload_company_file',
        entity='company_file',
        entity_id=company_file.id,
        diff={'company': company.name, 'filename': original_name, 'size': len(data)},
        ip=request.remote_addr,
    )
    return envelope(data=_serialize_company_file(company_file), status=201)


@blp.route('/admin/companies/<string:company_id>/files/<string:file_id>', methods=['PATCH'])
@roles_required('admin')
@blp.arguments(CompanyFilePatchSchema)
def patch_company_file(payload, company_id, file_id):
    company_file = db.session.get(CompanyFile, file_id)
    if not company_file or company_file.company_id != company_id:
        return envelope(error={'code': 'not_found', 'message': 'File not found', 'details': None}, status=404)

    diff = {}
    if payload.get('description') is not None:
        diff['description'] = {'old': company_file.description, 'new': payload['description']}
        company_file.description = payload['description']
    db.session.commit()

    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_update_company_file',
        entity='company_file',
        entity_id=file_id,
        diff=diff,
        ip=request.remote_addr,
    )
    return envelope(data=_serialize_company_file(company_file), status=200)


@blp.route('/admin/companies/<string:company_id>/files/<string:file_id>/download')
@roles_required('admin')
def download_company_file(company_id, file_id):
    from flask import Response

    company_file = db.session.get(CompanyFile, file_id)
    if not company_file or company_file.company_id != company_id:
        return envelope(error={'code': 'not_found', 'message': 'File not found', 'details': None}, status=404)

    doc = load_company_file(company_file.stored_file_id)
    if not doc:
        return envelope(error={'code': 'file_unavailable', 'message': 'File could not be retrieved', 'details': None}, status=404)

    data, mime = doc
    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_download_company_file',
        entity='company_file',
        entity_id=file_id,
        diff={'filename': company_file.original_filename},
        ip=request.remote_addr,
    )
    return Response(
        data,
        status=200,
        mimetype=mime,
        headers={'Content-Disposition': f'attachment; filename="{company_file.original_filename}"'},
    )


@blp.route('/admin/companies/<string:company_id>/files/<string:file_id>', methods=['DELETE'])
@roles_required('admin')
def delete_company_file(company_id, file_id):
    company_file = db.session.get(CompanyFile, file_id)
    if not company_file or company_file.company_id != company_id:
        return envelope(error={'code': 'not_found', 'message': 'File not found', 'details': None}, status=404)

    # Soft delete — archive it, leave the encrypted blob on disk. Same
    # reasoning as delete_company: reversible, matches the rest of the app.
    old_status = company_file.status
    company_file.status = 'archived'
    db.session.commit()

    log_audit_action(
        actor_user_id=g.current_user.id,
        action='admin_archive_company_file',
        entity='company_file',
        entity_id=file_id,
        diff={'filename': company_file.original_filename, 'status': {'old': old_status, 'new': 'archived'}},
        ip=request.remote_addr,
    )
    return envelope(data={'id': file_id}, status=200)


# ── Public projects list ──────────────────────────────────────────────────────

@blp.route('/projects')
def public_list_projects():
    """List all published projects for the public website."""
    items = Project.query.filter_by(status='published').order_by(Project.created_at.desc()).all()
    return envelope(data=[_serialize_project(p) for p in items], status=200)
