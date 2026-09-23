from __future__ import annotations

from flask import request
from flask_smorest import Blueprint

from app.extensions import limiter
from app.schemas.public import ChatRequestSchema
from app.services.audit_service import log_audit_action
from app.utils.response import envelope

from .service import process_message

blp = Blueprint('chat', __name__, description='Public chatbot')


@blp.route('/chat', methods=['POST'])
@blp.arguments(ChatRequestSchema)
# Per IP. The daily cap stops one visitor from draining the AI budget that
# every other visitor shares (CHAT_MAX_AI_CALLS_PER_DAY).
@limiter.limit('5/minute;60/day')
def chat(payload):
    result = process_message(
        message=payload['message'],
        session_id=payload.get('session_id') or request.headers.get('X-Chat-Session-Id'),
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
    )
    log_audit_action(
        action='chat_message',
        entity='chat_session',
        entity_id=result['session_id'],
        ip=request.remote_addr,
    )
    response, status = envelope(
        data={
            'reply':          result['reply'],
            'type':           result['type'],
            'action':         result['action'],
            'action_payload': result['action_payload'],
            'session_id':     result['session_id'],
            'ai_generated':   result['ai_generated'],
            'sources':        result['sources'],
        },
        status=200,
    )
    response.headers['X-Chat-Session-Id'] = result['session_id']
    return response, status
