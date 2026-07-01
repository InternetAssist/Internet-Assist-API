from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.logging import logger
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.services import ai_config_service
from app.services.audit_service import log_audit_action
from app.services.ticket_service import create_ticket

from .ai_gateway import call_ai

# ── Inline form definitions ───────────────────────────────────────────────────

FORMS = {
    'contact': {
        'form_type': 'contact',
        'submit_url': '/contact',
        'submit_method': 'POST',
        'fields': [
            {'name': 'name',    'label': 'Your Name',     'type': 'text',     'required': True},
            {'name': 'email',   'label': 'Email Address', 'type': 'email',    'required': True},
            {'name': 'phone',   'label': 'Phone Number',  'type': 'tel',      'required': False},
            {'name': 'company', 'label': 'Company',       'type': 'text',     'required': False},
            {'name': 'message', 'label': 'Message',       'type': 'textarea', 'required': True},
        ],
    },
    'quote': {
        'form_type': 'quote',
        'submit_url': '/quotes',
        'submit_method': 'POST',
        'fields': [
            {'name': 'name',      'label': 'Your Name',          'type': 'text',        'required': True},
            {'name': 'email',     'label': 'Email Address',      'type': 'email',       'required': True},
            {'name': 'phone',     'label': 'Phone Number',       'type': 'tel',         'required': False},
            {'name': 'company',   'label': 'Company Name',       'type': 'text',        'required': False},
            {'name': 'services',  'label': 'Services Required',  'type': 'multiselect', 'required': True,
             'options': ['Managed IT Support', 'Cybersecurity', 'Cloud Solutions', 'Networking', 'Business Continuity', 'VoIP']},
            {'name': 'team_size', 'label': 'Team Size',          'type': 'number',      'required': False},
            {'name': 'timeline',  'label': 'Timeline',           'type': 'text',        'required': False},
            {'name': 'details',   'label': 'Tell us about your needs', 'type': 'textarea', 'required': True},
        ],
    },
    'remote_support': {
        'form_type': 'remote_support',
        'submit_url': '/remote-support-request',
        'submit_method': 'POST',
        'fields': [
            {'name': 'name',  'label': 'Your Name',          'type': 'text',     'required': True},
            {'name': 'email', 'label': 'Email Address',      'type': 'email',    'required': True},
            {'name': 'phone', 'label': 'Phone Number',       'type': 'tel',      'required': False},
            {'name': 'issue', 'label': 'Describe your issue','type': 'textarea', 'required': True},
        ],
    },
    'job_application': {
        'form_type': 'job_application',
        'submit_url': '/job-applications',
        'submit_method': 'POST',
        'encoding': 'multipart/form-data',
        'fields': [
            {'name': 'fullName',     'label': 'Full Name',     'type': 'text',  'required': True},
            {'name': 'email',        'label': 'Email Address', 'type': 'email', 'required': True},
            {'name': 'phone',        'label': 'Phone Number',  'type': 'tel',   'required': False},
            {'name': 'position',     'label': 'Position',      'type': 'text',  'required': True},
            {'name': 'coverLetter',  'label': 'Cover Letter',  'type': 'textarea', 'required': False},
            {'name': 'cv',           'label': 'Upload CV',     'type': 'file',  'required': False,
             'accept': '.pdf,.doc,.docx'},
        ],
    },
}

# ── Session helpers ───────────────────────────────────────────────────────────

_MAX_HISTORY = 20  # messages sent to AI; caps token usage and prevents abuse


def _history_for_session(session: ChatSession) -> list[dict]:
    messages = session.messages[-_MAX_HISTORY:] if len(session.messages) > _MAX_HISTORY else session.messages
    return [{'role': m.role, 'content': m.content} for m in messages]


def get_or_create_session(session_id: str | None) -> ChatSession:
    if session_id:
        session = db.session.get(ChatSession, session_id)
        if session:
            return session
    return ChatSession()


# ── Ticket collection flow ────────────────────────────────────────────────────

_TICKET_FIELDS = [
    ('summary', 'Please provide a short summary of the issue.'),
    ('details', 'Please describe the issue in detail.'),
    ('email',   'Please provide your email address so we can update you.'),
    ('phone',   'Optional: provide a phone number (or type "skip").'),
]


def _handle_ticket_flow(session: ChatSession, message: str, ip: str | None) -> dict | None:
    """Continue an in-progress ticket-collection flow. Returns result dict or None if not in flow."""
    if session.ticket_flow_state != 'collect_ticket':
        return None

    data = session.ticket_flow_data or {}
    idx = len(data)
    field_name = _TICKET_FIELDS[idx][0]

    answer = message.strip()
    if field_name == 'email':
        answer = answer.lower()
        if '@' not in answer or '.' not in answer:
            reply = 'That email looks invalid. Please provide a valid email address.'
            _append_assistant(session, reply)
            db.session.commit()
            return {'reply': reply, 'type': 'create_ticket', 'action': 'await_ticket_input',
                    'action_payload': None, 'session_id': session.id}

    if field_name == 'phone' and answer.lower() == 'skip':
        answer = None

    data[field_name] = answer
    session.ticket_flow_data = data

    if len(data) < len(_TICKET_FIELDS):
        reply = _TICKET_FIELDS[len(data)][1]
        action = 'await_ticket_input'
        _append_assistant(session, reply)
        db.session.commit()
        return {'reply': reply, 'type': 'create_ticket', 'action': action,
                'action_payload': None, 'session_id': session.id}

    # All fields collected — submit ticket
    fields = {
        'Summary': data.get('summary'),
        'Details': data.get('details'),
        'Email':   data.get('email'),
        'Phone':   data.get('phone'),
    }
    try:
        ticket = create_ticket(ticket_type='chat', ticket_id=session.id,
                               fields=fields, sender_email=data.get('email'))
    except Exception:
        ticket = None

    if ticket:
        reply = (f"Thanks — I've created support ticket **{ticket.get('ticket_ref', '')}**. "
                 "Our team will contact you soon.")
        log_audit_action(action='chat_created_ticket', entity='ticket',
                         entity_id=ticket.get('ticket_id'), ip=ip)
    else:
        reply = ("Sorry, I couldn't create the ticket right now. "
                 "Please contact us directly at **enquiries@internetassist.co.uk** or **01621 840014**.")

    session.ticket_flow_state = None
    session.ticket_flow_data = None
    _append_assistant(session, reply)
    db.session.commit()
    return {'reply': reply, 'type': 'create_ticket', 'action': None,
            'action_payload': None, 'session_id': session.id}


def _append_assistant(session: ChatSession, content: str) -> None:
    session.messages.append(ChatMessage(role='assistant', content=content))


# ── Main entry point ──────────────────────────────────────────────────────────

def process_message(
    *,
    message: str,
    session_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    session = get_or_create_session(session_id)
    is_new = session_id is None or db.session.get(ChatSession, session_id) is None

    if is_new:
        session.started_at = datetime.now(timezone.utc)
    session.last_activity_at = datetime.now(timezone.utc)
    db.session.add(session)
    db.session.flush()

    session.messages.append(ChatMessage(role='user', content=message))

    # Continue ticket collection flow if active
    ticket_result = _handle_ticket_flow(session, message, ip)
    if ticket_result:
        return ticket_result

    # Start ticket flow on explicit request
    msg_lower = message.lower()
    if any(k in msg_lower for k in ('create ticket', 'raise ticket', 'open ticket', 'submit ticket', 'report issue')):
        if session.ticket_flow_state is None:
            session.ticket_flow_state = 'collect_ticket'
            session.ticket_flow_data = {}
            reply = _TICKET_FIELDS[0][1]
            _append_assistant(session, reply)
            db.session.commit()
            return {'reply': reply, 'type': 'create_ticket', 'action': 'await_ticket_input',
                    'action_payload': None, 'session_id': session.id}

    # AI handles everything else
    history = _history_for_session(session)
    action = None
    action_payload = None
    intent = 'ai'

    try:
        result = call_ai(
            message,
            history=history,
            model_name=current_app.config['AI_MODEL_NAME'],
            api_key=ai_config_service.resolve_api_key(),
        )
        reply = result.get('reply', '')
        ai_action = result.get('action')

        if ai_action == 'show_form':
            form_key = result.get('form', '')
            form_def = FORMS.get(form_key)
            if form_def:
                action = 'show_form'
                action_payload = form_def
                intent = form_key
            else:
                intent = 'ai'

        elif ai_action == 'redirect':
            action = 'redirect'
            action_payload = {
                'url':   result.get('url', '/'),
                'label': result.get('label', 'Go'),
            }
            intent = 'redirect'

        else:
            intent = 'ai'

    except Exception:
        reply = (
            'I am sorry, I could not process that right now. '
            f'Please call **{current_app.config["PUBLIC_CONTACT_PHONE"]}** or email '
            f'**{current_app.config["PUBLIC_CONTACT_EMAIL"]}**.'
        )

    _append_assistant(session, reply)
    db.session.commit()
    logger.info('chat_processed', session_id=session.id, intent=intent, action=action)

    return {
        'reply': reply,
        'type': intent,
        'action': action,
        'action_payload': action_payload,
        'session_id': session.id,
    }
