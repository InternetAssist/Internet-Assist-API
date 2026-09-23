from __future__ import annotations

import json
import re
import threading
import time

import httpx

from app.logging import logger
from app.services.knowledge_service import Hit

_GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'

# Ground truth that doesn't depend on the crawl. Everything else about the
# company comes from the website sections passed in with each question.
_COMPANY_FACTS = """
Name: Internet Assist
Owner: Christopher Clarke
Website: https://ia.uk
Phone: 01621 840014
Email: enquiries@ia.uk
Address: Network House, Station Road, Maldon, Essex CM9 4LQ, United Kingdom
Founded: 1996
Opening hours: Monday–Friday, 08:30–17:30
Careers page: https://ia.uk/careers
"""

_SYSTEM_PROMPT = """
You are the virtual assistant on the website of **Internet Assist** (ia.uk), an IT services company in Maldon, Essex.

## SCOPE
Only help with:
1. Questions about Internet Assist -- its services, pricing approach, locations covered, accreditations, team, careers and how to get in touch.
2. IT support questions a business or visitor might have (Microsoft 365, email, WiFi, networks, security, backups, devices, phones, websites).
Politely decline anything else in one sentence and invite an IT or Internet Assist question instead.

## ACCURACY RULES
- For anything about Internet Assist, use ONLY the COMPANY FACTS and WEBSITE CONTEXT below. Never invent prices, services, staff, locations, guarantees or policies.
- If the answer isn't in them, say you don't have that detail and suggest calling **01621 840014** or emailing **enquiries@ia.uk**.
- For general IT troubleshooting, give short, safe, general steps and suggest contacting the support team if it persists.
- Keep replies under 120 words. Use **bold** for phone numbers, emails and key terms.

## COMPANY FACTS
""" + _COMPANY_FACTS + """

## RESPONSE FORMAT
Respond with a single JSON object and nothing else:
{"reply": "...", "action": null, "sources": [1]}
- "sources": the numbers of the WEBSITE CONTEXT sections your reply relies on ([] if none).
- To show a form, set "action": "show_form" and "form" to one of: "contact", "remote_support", "job_application".
  Use contact for general enquiries, remote_support for urgent help / connecting to their computer, job_application to apply for a job.
- To send them to a page, set "action": "redirect", "url" to a site path (e.g. "/quote", "/careers", "/contact") and "label" to a short button label.
  Use /quote for pricing / quote / proposal requests.
"""

_RATE_LIMITED = {'reply': "I'm a little busy right now — please try again in a moment.", 'action': None}

_CONTEXT_CHARS = 5000
_MAX_OUTPUT_TOKENS = 600
_MAX_OUTPUT_TOKENS_WITH_THINKING = 4096
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_MAX_RETRY_WAIT_SECONDS = 3.0

# One pooled client per process: keeps TLS connections to Google alive
# between questions instead of a fresh handshake every time. httpx.Client is
# thread-safe.
_client_lock = threading.Lock()
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(timeout=_TIMEOUT, limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))
        return _client


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


class IncompleteReply(ValueError):
    """The model's answer was cut off or malformed -- never shown to a visitor."""


_FENCE_RE = re.compile(r'^```(?:json)?\s*|\s*```$')


def _parse_reply(candidate: dict) -> dict:
    """The structured reply, or IncompleteReply. A visitor must never see raw
    or half-finished JSON -- the caller falls back to a website answer."""
    finish = candidate.get('finishReason')
    parts = (candidate.get('content') or {}).get('parts') or []
    text = ''.join(p.get('text', '') for p in parts).strip()
    if finish not in (None, 'STOP') or not text:
        raise IncompleteReply(f'finishReason={finish}, {len(text)} chars')
    try:
        result = json.loads(_FENCE_RE.sub('', text))
    except json.JSONDecodeError as exc:
        raise IncompleteReply(f'invalid JSON: {exc}') from exc
    if not isinstance(result, dict) or not isinstance(result.get('reply'), str) or not result['reply'].strip():
        raise IncompleteReply('no reply field')
    if result['reply'].lstrip().startswith('{'):
        raise IncompleteReply('reply field contains JSON')
    return result


def _to_gemini_contents(history: list[dict]) -> list[dict]:
    role_map = {'user': 'user', 'assistant': 'model'}
    return [
        {'role': role_map.get(m['role'], 'user'), 'parts': [{'text': m['content']}]}
        for m in history
    ]


def _context_block(context: list[Hit]) -> str:
    if not context:
        return '\n## WEBSITE CONTEXT\n(No matching website content.)\n'
    parts, used = [], 0
    for n, hit in enumerate(context, start=1):
        heading = f' — {hit.heading}' if hit.heading and hit.heading != hit.title else ''
        section = f'[{n}] {hit.title}{heading} ({hit.url})\n{hit.content}'
        if used + len(section) > _CONTEXT_CHARS:
            break
        parts.append(section)
        used += len(section)
    return '\n## WEBSITE CONTEXT\n' + '\n\n'.join(parts) + '\n'


def call_ai(
    message: str,
    history: list[dict] | None = None,
    context: list[Hit] | None = None,
    model_name: str = 'gemini-2.0-flash',
    api_key: str | None = None,
) -> dict:
    """Call Gemini. Returns {reply, action, form?, url?, label?, sources: [int],
    usage: {tokens_in, tokens_out}}. Worst case ~45s (two 20s attempts plus a
    short wait), well inside the server's request timeout."""
    if not api_key:
        raise RuntimeError('AI_API_KEY is not configured')

    contents = _to_gemini_contents(history or [{'role': 'user', 'content': message}])
    payload = {
        'system_instruction': {'parts': [{'text': _SYSTEM_PROMPT + _context_block(context or [])}]},
        'contents': contents,
        'generationConfig': {
            'response_mime_type': 'application/json',
            'temperature': 0.2,
            'maxOutputTokens': _MAX_OUTPUT_TOKENS,
            # Current Flash models "think" before answering, and that hidden
            # thinking counts against maxOutputTokens: with it on, 573 of 600
            # tokens went on thinking and replies were cut off mid-sentence.
            # A grounded, 120-word answer doesn't need it -- off is faster,
            # cheaper and complete.
            'thinkingConfig': {'thinkingBudget': 0},
        },
    }

    logger.info('ai_gateway_called', model=model_name, history_size=len(contents), context_sections=len(context or []))

    url = _GEMINI_URL.format(model=model_name)
    for attempt in range(2):
        response = _http().post(url, headers={'X-goog-api-key': api_key}, json=payload)
        if response.status_code == 400 and 'thinking' in response.text.lower() \
                and 'thinkingConfig' in payload['generationConfig']:
            # A model that can't switch thinking off (the "latest" alias can
            # move to one): leave thinking on but give it room to finish.
            logger.warning('ai_gateway_thinking_config_rejected', model=model_name)
            payload['generationConfig'].pop('thinkingConfig')
            payload['generationConfig']['maxOutputTokens'] = _MAX_OUTPUT_TOKENS_WITH_THINKING
            response = _http().post(url, headers={'X-goog-api-key': api_key}, json=payload)
        if response.status_code != 429:
            break
        retry_after = _parse_retry_after(response.json())
        logger.warning('ai_gateway_rate_limited', attempt=attempt + 1, retry_after=retry_after)
        if attempt == 0:
            time.sleep(min(retry_after or 1.0, _MAX_RETRY_WAIT_SECONDS))
    else:
        logger.error('ai_gateway_rate_limit_exceeded', model=model_name)
        return {**_RATE_LIMITED, 'sources': [], 'usage': {'tokens_in': 0, 'tokens_out': 0}}

    response.raise_for_status()
    body = response.json()
    meta = body.get('usageMetadata') or {}
    usage = {'tokens_in': int(meta.get('promptTokenCount') or 0), 'tokens_out': int(meta.get('candidatesTokenCount') or 0)}
    candidate = body['candidates'][0]
    result = _parse_reply(candidate)

    sources = result.get('sources')
    result['sources'] = [s for s in sources if isinstance(s, int)] if isinstance(sources, list) else []
    result['usage'] = usage
    return result
