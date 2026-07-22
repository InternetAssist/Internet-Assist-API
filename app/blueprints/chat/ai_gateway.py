from __future__ import annotations

import json
import re
import time

import httpx

from app.logging import logger

_GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'

_COMPANY_FACTS = """
Name: Internet Assist
Owner: Christopher Clarke
Website: https://ia.uk
Phone: 01621 840014
Email: enquiries@ia.uk
Location: Maldon, Essex, United Kingdom
Founded: 29+ years ago
Services: Managed IT Support, Cybersecurity, Cloud Solutions (Microsoft 365 / Azure), Networking, Business Continuity & Disaster Recovery, VoIP & Communications
Customers: Small and medium-sized businesses across the UK
Careers page: https://ia.uk/careers
"""

_SYSTEM_PROMPT = """
You are the virtual assistant for **Internet Assist** (ia.uk), an IT support company based in Maldon, Essex.

## YOUR JOB
Help website visitors with:
1. Questions about Internet Assist — use ONLY the company facts below, never invent details.
2. General IT questions (WiFi, hardware, software, email, security, networking) — answer helpfully from your knowledge.
3. Recognising when a visitor wants to take action and triggering the right form or redirect.

## COMPANY FACTS
""" + _COMPANY_FACTS + """

## RESPONSE FORMAT
You MUST always respond with a single valid JSON object — nothing outside it.

### Plain reply (most questions):
{"reply": "your response", "action": null}

### Show inline form — contact / general enquiry:
{"reply": "I'll get the contact form ready for you.", "action": "show_form", "form": "contact"}

### Redirect — request a quote / pricing / proposal / cost:
{"reply": "I'll take you to our quote request page where you can tell us about your needs.", "action": "redirect", "url": "/quote", "label": "Get a Quote"}

### Show inline form — remote support / urgent help / connect to computer:
{"reply": "Let me connect you with our support team right away.", "action": "show_form", "form": "remote_support"}

### Show inline form — job application:
{"reply": "Great — let me pull up the job application form.", "action": "show_form", "form": "job_application"}

### Redirect — careers / vacancies / job listings:
{"reply": "Check out our current openings on the careers page.", "action": "redirect", "url": "/careers", "label": "View Open Positions"}

## RULES
- reply: concise, friendly, professional. Use **bold** for proper nouns, company names, phone numbers, emails, and URLs.
- For company questions use ONLY the company facts. Say "I don't have that information — please contact us at **enquiries@ia.uk** or call **01621 840014**." if not covered.
- For general IT questions answer helpfully.
- Trigger a form whenever the visitor's intent is to submit information or get help that requires their details.
- NEVER output anything outside the JSON object.
"""

_RATE_LIMITED = {'reply': "I'm a little busy right now — please try again in a moment.", 'action': None}


def _parse_retry_after(body: dict) -> float:
    """Extract retry-after seconds from a Gemini 429 response body."""
    try:
        msg = body.get('error', {}).get('message', '')
        match = re.search(r'retry in ([\d.]+)s', msg)
        if match:
            return float(match.group(1))
    except Exception:
        pass
    return 0.0


def _to_gemini_contents(history: list[dict]) -> list[dict]:
    role_map = {'user': 'user', 'assistant': 'model'}
    return [
        {'role': role_map.get(m['role'], 'user'), 'parts': [{'text': m['content']}]}
        for m in history
    ]


def call_ai(
    message: str,
    history: list[dict] | None = None,
    model_name: str = 'gemini-2.0-flash',
    api_key: str | None = None,
) -> dict:
    """Call Gemini and return a structured dict: {reply, action, form?, url?, label?}"""
    if not api_key:
        raise RuntimeError('AI_API_KEY is not configured')

    contents = _to_gemini_contents(history or [{'role': 'user', 'content': message}])
    payload = {
        'system_instruction': {'parts': [{'text': _SYSTEM_PROMPT}]},
        'contents': contents,
        'generationConfig': {
            'response_mime_type': 'application/json',
            'temperature': 0.4,
        },
    }

    logger.info('ai_gateway_called', model=model_name, history_size=len(contents))

    url = _GEMINI_URL.format(model=model_name)
    for attempt in range(3):
        response = httpx.post(url, headers={'X-goog-api-key': api_key}, json=payload, timeout=30)
        if response.status_code != 429:
            break

        retry_after = _parse_retry_after(response.json())
        logger.warning('ai_gateway_rate_limited', attempt=attempt + 1, retry_after=retry_after)

        # Only wait if within a budget we can afford (gunicorn timeout is 120s)
        wait = min(retry_after or (2 ** (attempt + 1)), 10.0)
        if wait > 0:
            time.sleep(wait)
    else:
        # All attempts exhausted — return a friendly message instead of raising
        logger.error('ai_gateway_rate_limit_exceeded', model=model_name)
        return _RATE_LIMITED

    response.raise_for_status()
    text = response.json()['candidates'][0]['content']['parts'][0]['text']

    try:
        result = json.loads(text)
        if 'reply' not in result:
            result = {'reply': text, 'action': None}
    except (json.JSONDecodeError, KeyError):
        result = {'reply': text, 'action': None}

    return result
