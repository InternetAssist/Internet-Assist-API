from __future__ import annotations

from flask import request
from flask_smorest import Blueprint

from app.extensions import db, limiter
from app.models.contact import Contact
from app.schemas.public import RemoteSupportRequestSchema
from app.services.audit_service import log_audit_action
from app.services.email_service import send_confirmation, send_ticket
from app.services.ticket_service import create_ticket
from app.utils.response import envelope

blp = Blueprint('public-remote-support', __name__, description='Remote support requests')


@blp.route('/remote-support-request', methods=['POST'])
@blp.arguments(RemoteSupportRequestSchema)
@limiter.limit('5/minute')
def create_remote_support(payload):
    contact = Contact(
        name=payload['name'],
        email=payload['email'].lower(),
        phone=payload.get('phone'),
        message=payload['issue'],
        source='remote_support',
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
    )
    db.session.add(contact)
    db.session.commit()

    ticket = create_ticket(
        ticket_type='remote_support',
        ticket_id=contact.id,
        fields={
            'Name':  contact.name,
            'Email': contact.email,
            'Phone': contact.phone,
            'Issue': contact.message,
        },
        sender_email=contact.email,
        sender_name=contact.name,
    )
    if ticket:
        contact.ticket_id  = ticket['ticket_id']
        contact.ticket_ref = ticket['ticket_ref']
        db.session.commit()

    try:
        send_ticket(
            ticket_type='remote_support',
            ticket_id=contact.id,
            fields={
                'Name':  contact.name,
                'Email': contact.email,
                'Phone': contact.phone,
                'Issue': contact.message,
            },
            user_email=contact.email,
        )
    except Exception:
        pass

    try:
        send_confirmation(ticket_type='remote_support', recipient_email=contact.email, recipient_name=contact.name, ticket_ref=contact.ticket_ref)
    except Exception:
        pass

    log_audit_action(action='remote_support_requested', entity='contact', entity_id=contact.id, ip=request.remote_addr)
    return envelope(data={'id': contact.id, 'status': contact.status, 'ticket_ref': contact.ticket_ref}, status=201)
