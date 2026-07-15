from __future__ import annotations

from uuid import uuid4

from flask import request
from flask_smorest import Blueprint

from app.extensions import db, limiter
from app.models.contact import Contact
from app.schemas.public import ContactCreateSchema
from app.services.audit_service import log_audit_action
from app.services.email_service import send_confirmation, send_ticket
from app.services.ticket_service import create_ticket
from app.utils.response import envelope

blp = Blueprint('public-contact', __name__, description='Contact submissions')


@blp.route('/contact', methods=['POST'])
@blp.arguments(ContactCreateSchema)
@limiter.limit('10/minute')
def create_contact(payload):
    # Honeypot tripped -- pretend success so the bot doesn't adjust its
    # behaviour, but skip the DB write and every email entirely.
    if payload.get('website'):
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
