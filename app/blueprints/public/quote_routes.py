from __future__ import annotations

from flask import request
from flask_smorest import Blueprint

from app.extensions import db, limiter
from app.models.quote import Quote
from app.schemas.public import QuoteCreateSchema
from app.services.audit_service import log_audit_action
from app.services.email_service import send_ticket
from app.services.ticket_service import create_ticket
from app.utils.response import envelope

blp = Blueprint('public-quotes', __name__, description='Quote requests')


@blp.route('/quotes', methods=['POST'])
@blp.arguments(QuoteCreateSchema)
@limiter.limit('10/minute')
def create_quote(payload):
    quote = Quote(
        name=payload['name'],
        email=payload['email'].lower(),
        phone=payload.get('phone'),
        company=payload.get('company'),
        services=payload['services'],
        team_size=payload.get('team_size'),
        timeline=payload.get('timeline'),
        details=payload['details'],
    )
    db.session.add(quote)
    db.session.commit()

    ticket = create_ticket(
        ticket_type='quote',
        ticket_id=quote.id,
        fields={
            'Name':      quote.name,
            'Email':     quote.email,
            'Phone':     quote.phone,
            'Company':   quote.company,
            'Services':  ', '.join(quote.services or []),
            'Team Size': quote.team_size,
            'Timeline':  quote.timeline,
            'Details':   quote.details,
        },
        sender_email=quote.email,
        sender_name=quote.name,
    )
    if ticket:
        quote.ticket_id  = ticket['ticket_id']
        quote.ticket_ref = ticket['ticket_ref']
        db.session.commit()

    try:
        send_ticket(
            ticket_type='quote',
            ticket_id=quote.id,
            fields={
                'Name':      quote.name,
                'Email':     quote.email,
                'Phone':     quote.phone,
                'Company':   quote.company,
                'Services':  ', '.join(quote.services or []),
                'Team Size': quote.team_size,
                'Timeline':  quote.timeline,
                'Details':   quote.details,
            },
            user_email=quote.email,
        )
    except Exception:
        pass

    log_audit_action(action='public_quote_created', entity='quote', entity_id=quote.id, ip=request.remote_addr)
    return envelope(data={'id': quote.id, 'status': quote.status, 'ticket_ref': quote.ticket_ref}, status=201)
