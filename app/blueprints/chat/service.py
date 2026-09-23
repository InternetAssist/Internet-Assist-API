from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from flask import current_app

from app.extensions import db
from app.logging import logger
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.services import ai_config_service, ai_usage_service, chat_cache_service, chat_guard, knowledge_service, site_crawler
from app.services.audit_service import log_audit_action
from app.services.ticket_service import create_ticket

from .ai_gateway import _RATE_LIMITED, IncompleteReply, call_ai

_RATE_LIMITED_REPLY = _RATE_LIMITED['reply']

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

# ── Redirect validation ───────────────────────────────────────────────────────
# The redirect URL comes from the model (and from the reply cache, which is
# shared across visitors), so a prompt-injected message could otherwise push
# an external phishing link to everyone who asks a similar question. Only
# same-site paths like /quote or /careers are allowed.

_SAFE_REDIRECT_RE = re.compile(r'^/[a-z0-9-]+(?:/[a-z0-9-]+)*/?$')


def _safe_redirect(payload: dict | None) -> dict | None:
    url = (payload or {}).get('url')
    if not isinstance(url, str) or not _SAFE_REDIRECT_RE.match(url):
        return None
    label = str((payload or {}).get('label') or 'Go')[:60]
    return {'url': url, 'label': label}


# ── Session helpers ───────────────────────────────────────────────────────────

# Messages sent to the AI as conversation history. Each one costs input
# tokens on every call, and the website context already carries the facts.
_MAX_HISTORY = 6
_CONTEXT_SECTIONS = 5
_FOLLOWUP_WINDOW = timedelta(minutes=15)


def _recent_history(session: ChatSession) -> list[dict]:
    """Last _MAX_HISTORY messages, oldest first. Queried with a LIMIT rather
    than loading session.messages -- a long conversation used to be read into
    memory in full on every message."""
    rows = (
        ChatMessage.query.filter_by(session_id=session.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(_MAX_HISTORY)
        .all()
    )
    return [{'role': m.role, 'content': m.content} for m in reversed(rows)]


def get_or_create_session(session_id: str | None) -> tuple[ChatSession, bool]:
    if session_id:
        session = db.session.get(ChatSession, session_id)
        if session:
            return session, False
    return ChatSession(), True


def _add_message(session: ChatSession, role: str, content: str, tokens_in: int = 0, tokens_out: int = 0) -> None:
    db.session.add(ChatMessage(session_id=session.id, role=role, content=content,
                               tokens_in=tokens_in, tokens_out=tokens_out))


def _append_assistant(session: ChatSession, content: str) -> None:
    _add_message(session, 'assistant', content)


def _result(session: ChatSession, reply: str, intent: str, *, action=None, action_payload=None,
            ai_generated: bool = False, sources: list[dict] | None = None) -> dict:
    return {
        'reply': reply,
        'type': intent,
        'action': action,
        'action_payload': action_payload,
        'session_id': session.id,
        # The frontend labels these as AI-written so visitors know to verify.
        'ai_generated': ai_generated,
        'sources': sources or [],
    }


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

    # Copy: reassigning the same (mutated) dict isn't seen as a change by the
    # ORM, so the collected answers would never be saved.
    data = dict(session.ticket_flow_data or {})
    idx = len(data)
    field_name = _TICKET_FIELDS[idx][0]

    answer = message.strip()
    if field_name == 'email':
        answer = answer.lower()
        if '@' not in answer or '.' not in answer:
            reply = 'That email looks invalid. Please provide a valid email address.'
            _append_assistant(session, reply)
            db.session.commit()
            return _result(session, reply, 'create_ticket', action='await_ticket_input')

    if field_name == 'phone' and answer.lower() == 'skip':
        answer = None

    data[field_name] = answer
    session.ticket_flow_data = data

    if len(data) < len(_TICKET_FIELDS):
        reply = _TICKET_FIELDS[len(data)][1]
        _append_assistant(session, reply)
        db.session.commit()
        return _result(session, reply, 'create_ticket', action='await_ticket_input')

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
                 f"Please contact us directly at **{_contact_email()}** or **{_contact_phone()}**.")

    session.ticket_flow_state = None
    session.ticket_flow_data = None
    _append_assistant(session, reply)
    db.session.commit()
    return _result(session, reply, 'create_ticket')


# ── Replies that never touch the AI ───────────────────────────────────────────

def _contact_phone() -> str:
    return current_app.config['PUBLIC_CONTACT_PHONE']


def _contact_email() -> str:
    return current_app.config['PUBLIC_CONTACT_EMAIL']


def _local_reply(kind: str) -> str:
    if kind == chat_guard.GREETING:
        return ("Hi! I'm the Internet Assist assistant. Ask me about our IT support, cloud, cyber security, "
                "backup, phone systems, web design or careers — or say **create ticket** to raise a support request.")
    if kind == chat_guard.THANKS:
        return "You're welcome! Is there anything else I can help you with?"
    if kind == chat_guard.UNCLEAR:
        return "Could you tell me a little more about what you need help with?"
    if kind == chat_guard.OFF_TOPIC:
        return ("Sorry, I can only help with questions about **Internet Assist** and IT support — for example "
                "our services, pricing, the areas we cover, careers, or a technical problem you're having. "
                "Could you ask me something along those lines?")
    # AI budget reached
    return ("I can't answer any more questions here right now. Please call "
            f"**{_contact_phone()}** or email **{_contact_email()}** and our team will be happy to help.")


def _sources_for(indices: list[int], context: list[knowledge_service.Hit]) -> list[dict]:
    """Map the section numbers the AI cited back to real pages. Only pages we
    actually gave it can come back -- the AI can't invent a source link."""
    out, seen = [], set()
    for n in indices:
        if 1 <= n <= len(context):
            hit = context[n - 1]
            if hit.url not in seen:
                seen.add(hit.url)
                out.append({'title': hit.title, 'url': hit.url})
    return out


_FALLBACK_MAX_LINES = 10
_FALLBACK_MAX_CHARS = 450


_MIN_ANSWER_COVERAGE = 0.5


def _confident_hit(context: list[knowledge_service.Hit], query: str) -> knowledge_service.Hit | None:
    """The section to quote, or None if nothing matches the question well
    enough to quote without an AI checking it. context comes from
    knowledge_service.relevant_sections (town pages the question doesn't
    name are already gone). The winner must cover at least half the
    question's words, and every rare, site-specific word it uses -- "Where
    is your office located" must not be answered by a section that only
    shares the word "office"."""
    if not context:
        return None
    # A recognised common question ("where are you based?") was routed to
    # the page that answers it -- relevant_sections puts that page first.
    target = knowledge_service.route(query)
    if target is not None and context[0].url == target:
        return context[0]

    query_tokens = set(knowledge_service.tokenize(query))
    if not query_tokens:
        return None
    rare = query_tokens & knowledge_service.distinctive_terms()

    def coverage(hit):
        return len(hit.matched & query_tokens) / len(query_tokens)

    def names_it(hit):
        # "Do you cover Chelmsford?" -> prefer the Chelmsford page itself.
        return bool(rare & knowledge_service.page_words(hit))

    def about_it(hit):
        # The page *about* the topic (/it-support for "do you offer IT
        # support?") beats pages that merely mention it, like a list of the
        # towns we offer IT support in.
        return len(query_tokens & set(knowledge_service.tokenize(hit.url.replace('-', ' ').replace('/', ' '))))

    # Meaning first, when calibrated: a section whose meaning is close enough
    # to the question can be quoted even if it shares few words with it --
    # but names ("Chelmsford", "Hornetsecurity") must still literally match
    # (see knowledge_service.names_in).
    thresholds = knowledge_service.semantic_thresholds()
    if thresholds and thresholds.get('answer') is not None:
        names = knowledge_service.names_in(query)
        close = [h for h in context
                 if h.similarity is not None and h.similarity >= thresholds['answer'] and names <= h.matched]
        if close:
            best = max(close, key=lambda h: (names_it(h), about_it(h), h.similarity))
            return _page_intro_if_about(best, query_tokens, about_it)

    best = max(context, key=lambda h: (rare <= h.matched, names_it(h), about_it(h), coverage(h), h.score))
    if coverage(best) < _MIN_ANSWER_COVERAGE or not rare <= best.matched:
        return None
    return _page_intro_if_about(best, query_tokens, about_it)


def _page_intro_if_about(best, query_tokens: set[str], about_it) -> knowledge_service.Hit:
    """"Do you offer IT support?" is about the /it-support page as a whole --
    its introduction answers it better than whichever fragment of it
    matched ("FREE IT health check...")."""
    if about_it(best) == len(query_tokens):
        intro = knowledge_service.page_intro(best.url)
        if intro is not None:
            return intro
    return best


def _website_answer(context: list[knowledge_service.Hit], query: str) -> tuple[str, list[dict]] | None:
    """A reply quoted straight from the best-matching website section, for
    when the AI can't be used (no key, Gemini down or rate-limited, budget
    reached). None when no section matches the question confidently -- a
    wrong answer is worse than saying so."""
    best = _confident_hit(context, query)
    if best is None:
        return None
    lines = [line.strip() for line in best.content.split('\n') if line.strip()]
    if len(lines) > 1:
        # Lists (services, areas covered, features) read best as bullets.
        body = '\n'.join(f'- {line[:160]}' for line in lines[:_FALLBACK_MAX_LINES])
    else:
        text = lines[0] if lines else ''
        if len(text) > _FALLBACK_MAX_CHARS:
            cut = text.rfind('. ', 0, _FALLBACK_MAX_CHARS)
            text = text[:cut + 1] if cut > 0 else text[:_FALLBACK_MAX_CHARS].rstrip() + '…'
        body = text
    topic = best.heading or best.title
    reply = (
        f"Here's what our website says about **{topic}**:\n\n{body}\n\n"
        f"For anything more specific, call **{_contact_phone()}** or email **{_contact_email()}**."
    )
    return reply, [{'title': best.title, 'url': best.url}]


def _no_answer_reply() -> str:
    return (
        "I couldn't find a clear answer to that on our website, and I'd rather not guess. "
        f"Please call **{_contact_phone()}** or email **{_contact_email()}** and our team will help."
    )


def _support_offer(session: ChatSession, urgent: bool) -> dict:
    """Someone who needs help gets the support line and the remote-support
    form -- never a quoted web page, and no AI call."""
    opener = "I'm sorry you're having trouble." if urgent else "Sorry to hear you're having problems."
    reply = (
        f"{opener} For anything urgent, call our support team now on **{_contact_phone()}** "
        "(Monday–Friday, 08:30–17:30). Or send us the details below and an engineer will get back to you — "
        "you can also type **create ticket** to raise a support ticket here."
    )
    _append_assistant(session, reply)
    db.session.commit()
    return _result(session, reply, 'remote_support', action='show_form', action_payload=FORMS['remote_support'])


def _reply_from_website(session: ChatSession, context: list[knowledge_service.Hit], query: str,
                        fallback_reply: str, fallback_intent: str) -> dict:
    """Answer from the website if a section matches confidently, otherwise with
    fallback_reply. A technical problem gets the support offer instead -- a
    page about our services doesn't help someone whose email is down."""
    if chat_guard.support_intent(query):
        return _support_offer(session, urgent=False)
    answer = _website_answer(context, query)
    reply, sources, intent = (answer[0], answer[1], 'website') if answer else (fallback_reply, [], fallback_intent)
    _append_assistant(session, reply)
    db.session.commit()
    return _result(session, reply, intent, sources=sources)


def _form_or_redirect(ai_action: str | None, form_key: str | None, redirect: dict | None) -> tuple[str | None, dict | None, str]:
    """Returns (action, action_payload, intent) for an AI- or cache-proposed action."""
    if ai_action == 'show_form':
        form_def = FORMS.get(form_key or '')
        if form_def:
            return 'show_form', form_def, form_key
    elif ai_action == 'redirect':
        safe = _safe_redirect(redirect)
        if safe:
            return 'redirect', safe, 'redirect'
        logger.warning('chat_redirect_rejected', url=str((redirect or {}).get('url'))[:200])
    return None, None, 'ai'


# ── Main entry point ──────────────────────────────────────────────────────────

def process_message(
    *,
    message: str,
    session_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    session, is_new = get_or_create_session(session_id)
    if is_new:
        session.started_at = now
    session.last_activity_at = now
    db.session.add(session)
    db.session.flush()

    history_before = _recent_history(session)
    _add_message(session, 'user', message)

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
            return _result(session, reply, 'create_ticket', action='await_ticket_input')

    # Someone asking for help right now gets the support line and form
    # straight away -- no AI, no page quote.
    if chat_guard.support_intent(message) == chat_guard.URGENT:
        session.last_relevant_at = now
        result = _support_offer(session, urgent=True)
        ai_usage_service.increment(local_replies=1)
        logger.info('chat_processed', session_id=session.id, intent='urgent_support', ai=False)
        return result

    # Keep the website knowledge fresh (background, never blocks this reply).
    try:
        site_crawler.refresh_if_stale()
    except Exception:
        logger.exception('chat_knowledge_refresh_check_failed')

    # ── Relevance gate: decide locally whether this deserves an AI call ─────
    last_relevant = session.last_relevant_at
    if last_relevant is not None and last_relevant.tzinfo is None:
        last_relevant = last_relevant.replace(tzinfo=timezone.utc)
    in_conversation = bool(last_relevant and now - last_relevant < _FOLLOWUP_WINDOW)

    # A short follow-up ("what about for 10 users?") is searched together
    # with the question before it, so it finds the same pages.
    previous_user = next((m['content'] for m in reversed(history_before) if m['role'] == 'user'), '')
    search_text = f'{previous_user} {message}' if in_conversation else message
    context = knowledge_service.relevant_sections(search_text, limit=_CONTEXT_SECTIONS)
    kind = chat_guard.classify(message, knowledge_service.search(message, limit=1), in_conversation)

    if kind != chat_guard.RELEVANT:
        reply = _local_reply(kind)
        _append_assistant(session, reply)
        db.session.commit()
        ai_usage_service.increment(**{'off_topic' if kind == chat_guard.OFF_TOPIC else 'local_replies': 1})
        logger.info('chat_processed', session_id=session.id, intent=kind, ai=False)
        return _result(session, reply, kind)

    # The first on-topic question of a session (greetings before it don't
    # count) doesn't depend on earlier context, so it can use -- and seed --
    # the shared answer cache. Later questions might be follow-ups.
    context_free = last_relevant is None
    session.last_relevant_at = now

    # ── Cached answer ────────────────────────────────────────────────────────
    # A cached reply was generated in isolation, so reusing it for something
    # that depends on earlier conversation context could answer the wrong thing.
    if context_free:
        cache_hit = chat_cache_service.find_cached_reply(message)
        if cache_hit:
            chat_cache_service.record_hit(cache_hit)
            payload = cache_hit.action_payload or {}
            action, action_payload, intent = _form_or_redirect(cache_hit.action, payload.get('form_type'), payload)
            _append_assistant(session, cache_hit.reply)
            db.session.commit()
            ai_usage_service.increment(cache_hits=1)
            logger.info('chat_processed', session_id=session.id, intent=intent, ai=False, cached=True)
            return _result(session, cache_hit.reply, intent, action=action, action_payload=action_payload,
                           ai_generated=True, sources=cache_hit.sources)

    # ── AI budget ────────────────────────────────────────────────────────────
    cfg = current_app.config
    if (session.ai_calls or 0) >= cfg['CHAT_MAX_AI_CALLS_PER_SESSION'] or \
            ai_usage_service.today()['ai_calls'] >= cfg['CHAT_MAX_AI_CALLS_PER_DAY']:
        result = _reply_from_website(session, context, message, _local_reply('budget'), 'budget')
        ai_usage_service.increment(budget_blocked=1)
        logger.warning('chat_ai_budget_reached', session_id=session.id, session_calls=session.ai_calls)
        return result

    # ── Grounded AI answer ───────────────────────────────────────────────────
    history = history_before + [{'role': 'user', 'content': message}]
    try:
        result = call_ai(
            message,
            history=history,
            context=context,
            model_name=cfg['AI_MODEL_NAME'],
            api_key=ai_config_service.resolve_api_key(),
        )
    except Exception as exc:
        # No key, Gemini down, bad response -- still answer from the website.
        ai_config_service.record_call_result(False, str(exc)[:200])
        logger.warning('chat_ai_unavailable', session_id=session.id, error=str(exc)[:200])
        if isinstance(exc, IncompleteReply):
            # Gemini answered (and billed) but the reply was unusable -- it
            # still counts against the budgets, or a misbehaving model could
            # spend without the daily/session caps ever triggering.
            session.ai_calls = (session.ai_calls or 0) + 1
            result = _reply_from_website(session, context, message, _no_answer_reply(), 'no_answer')
            ai_usage_service.increment(ai_calls=1)
            return result
        return _reply_from_website(session, context, message, _no_answer_reply(), 'no_answer')

    usage = result.get('usage') or {}
    session.ai_calls = (session.ai_calls or 0) + 1

    reply = result.get('reply', '')
    # call_ai() returns the rate-limited fallback as a normal (non-exception)
    # result once retries are exhausted -- a successful *call* isn't a
    # successful *reply*.
    rate_limited = reply == _RATE_LIMITED_REPLY
    ai_config_service.record_call_result(not rate_limited, 'rate_limited' if rate_limited else None)
    if rate_limited:
        db.session.commit()
        ai_usage_service.increment(ai_calls=1, tokens_in=usage.get('tokens_in', 0), tokens_out=usage.get('tokens_out', 0))
        return _reply_from_website(session, context, message, _no_answer_reply(), 'no_answer')

    action, action_payload, intent = _form_or_redirect(
        result.get('action'), result.get('form'), {'url': result.get('url'), 'label': result.get('label')},
    )
    sources = _sources_for(result.get('sources') or [], context)

    if context_free:
        chat_cache_service.store_reply(
            message=message,
            reply=reply,
            action=action,
            action_payload=action_payload,
            model_name=cfg['AI_MODEL_NAME'],
            sources=sources,
        )

    _add_message(session, 'assistant', reply, usage.get('tokens_in', 0), usage.get('tokens_out', 0))
    db.session.commit()
    ai_usage_service.increment(ai_calls=1, tokens_in=usage.get('tokens_in', 0), tokens_out=usage.get('tokens_out', 0))
    logger.info('chat_processed', session_id=session.id, intent=intent, action=action, ai=True,
                tokens_in=usage.get('tokens_in'), tokens_out=usage.get('tokens_out'), sources=len(sources))

    return _result(session, reply, intent, action=action, action_payload=action_payload,
                   ai_generated=True, sources=sources)
