from __future__ import annotations

from uuid import uuid4

from flask import request
from flask_smorest import Blueprint

from app.extensions import db, limiter
from app.models.contact import Contact
from app.schemas.public import ContactCreateSchema
from app.services.audit_service import log_audit_action
from app.services.email_service import send_confirmation, send_ticket
from app.services.recaptcha_service import verify_recaptcha
from app.services.spam_signals_service import has_excessive_links, is_tor_exit_node
from app.services.ticket_service import create_ticket
from app.utils.response import envelope

blp = Blueprint('public-contact', __name__, description='Contact submissions')


@blp.route('/contact', methods=['POST'])
@blp.arguments(ContactCreateSchema)
@limiter.limit('10/minute')
def create_contact(payload):
    # Honeypot tripped, reCAPTCHA thinks this is a bot, the request is from a
    # known Tor exit node, or the message is stuffed with links -- pretend
    # success so it doesn't adjust its behaviour, but skip the DB write and
    # every email.
    origin = request.headers.get('Origin') or request.headers.get('Referer')
    is_spam = (
        payload.get('website')
        or not verify_recaptcha(payload.get('recaptcha_token'), request.remote_addr, origin)
        or is_tor_exit_node(request.remote_addr)
        or has_excessive_links(payload.get('message'))
    )
    if is_spam:
        return envelope(data={'id': uuid4().hex, 'status': 'new', 'ticket_ref': None}, status=201)

    contact = Contact(
        name=payload['name'],
        email=payload['email'].lower(),
        phone=payload.get('phone'),
        company=payload.get('company'),
        message=payload['message'],
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
    )
    db.session.add(contact)
    db.session.commit()

    fields = {
        'Name':    contact.name,
        'Email':   contact.email,
        'Phone':   contact.phone,
        'Company': contact.company,
        'Message': contact.message,
    }

    ticket = create_ticket(
        ticket_type='contact',
        ticket_id=contact.id,
        fields=fields,
        sender_email=contact.email,
        sender_name=contact.name,
    )
    if ticket:
        contact.ticket_id  = ticket['ticket_id']
        contact.ticket_ref = ticket['ticket_ref']
        db.session.commit()

    try:
        send_ticket(ticket_type='contact', ticket_id=contact.id, fields=fields, user_email=contact.email)
    except Exception:
        pass

    try:
        send_confirmation(
            ticket_type='contact',
            recipient_email=contact.email,
            recipient_name=contact.name,
            ticket_ref=contact.ticket_ref,
            details=fields,
        )
    except Exception:
        pass

    log_audit_action(action='public_contact_created', entity='contact', entity_id=contact.id, ip=request.remote_addr)
    return envelope(data={'id': contact.id, 'status': contact.status, 'ticket_ref': contact.ticket_ref}, status=201)
