from __future__ import annotations

import re

from app.extensions import db
from app.logging import logger
from app.models.chat_qa_cache import ChatQaCache
from app.models.site_setting import SiteSetting

_CONFIG_KEY = 'chat_cache_config'

# Below this, two questions are treated as different enough that reusing a
# cached reply risks answering the wrong thing -- better to fall through to
# a real Gemini call than serve a confidently wrong cached answer. Jaccard
# over stopword-filtered tokens rather than raw character/word sequences --
# real visitors paraphrase ("what services do you offer" vs "what kind of
# services do you guys offer"), and character-level diffing scored that
# pair at 0.79 even though they're the same question; token overlap after
# dropping filler words scores it 1.0.
#
# This whole config block is only the SEED value written to the site_settings
# table the first time it's read -- from then on it lives in the database
# (SiteSetting, key='chat_cache_config'), not in this source file, matching
# how ai_config_service stores the Gemini key. Edit it in the DB to change
# behaviour without a deploy.
_DEFAULT_CONFIG = {
    'match_threshold': 0.6,
    'stopwords': sorted([
        'a', 'an', 'the', 'do', 'does', 'did', 'you', 'your', 'yours', 'is', 'are',
        'was', 'were', 'of', 'to', 'for', 'and', 'or', 'my', 'me', 'i', 'what',
        'which', 'who', 'how', 'can', 'could', 'would', 'should', 'please',
        'kind', 'sort', 'guys', 'we', 'us', 'our', 'it', 'on', 'in', 'at',
        'about', 'with', 'that', 'this', 'have', 'has', 'need', 'want', 'like',
    ]),
    # Token overlap alone still misses same-issue questions phrased with
    # different words ("wifi is off" vs "wifi isn't working" share zero
    # tokens after stopword removal). Small, hand-curated synonym groups for
    # this chatbot's actual domain (IT support) close that gap without
    # pulling in embeddings -- deliberately conservative so unrelated topics
    # don't collapse into each other. Extend these in the DB as real
    # mismatches turn up.
    'synonym_groups': [
        sorted(['down', 'off', 'broken', 'dead', 'unavailable', 'working', 'work']),
        sorted(['slow', 'laggy', 'sluggish', 'lagging']),
        sorted(['price', 'pricing', 'cost', 'costs', 'charge', 'charges', 'fee', 'fees']),
        sorted(['email', 'mail', 'e-mail', 'emails']),
        sorted(['wifi', 'wi-fi', 'wireless']),
        sorted(['password', 'passwd', 'pwd', 'login', 'log-in']),
        sorted(['computer', 'pc', 'laptop', 'machine', 'desktop']),
        sorted(['internet', 'connection', 'network']),
    ],
}

_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_PHONE_RE = re.compile(r'(?:\+?\d[\d\s\-().]{7,}\d)')

# Never cache these -- they're not real answers, just failure fallbacks from
# ai_gateway.py, and caching them would make the fallback sticky for anyone
# who happens to ask something similar afterwards.
_UNCACHEABLE_REPLIES = {
    "I'm a little busy right now — please try again in a moment.",
}


def _config() -> dict:
    existing = SiteSetting.get(_CONFIG_KEY)
    if existing is not None:
        return existing
    # First-ever read: seed the DB with the default config so it's the
    # database, not this file, that's authoritative from now on.
    SiteSetting.upsert(_CONFIG_KEY, _DEFAULT_CONFIG)
    logger.info('chat_cache_config_seeded')
    return _DEFAULT_CONFIG


def _synonym_map(config: dict) -> dict[str, str]:
    return {word: group[0] for group in config['synonym_groups'] for word in group}


def _normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', text.strip().lower())


def _significant_tokens(normalized_text: str, config: dict) -> set[str]:
    stopwords = set(config['stopwords'])
    synonyms = _synonym_map(config)
    words = re.findall(r"[a-z0-9']+", normalized_text)
    return {synonyms.get(w, w) for w in words if w not in stopwords}


def _similarity(a_normalized: str, b_normalized: str, config: dict) -> float:
    tokens_a = _significant_tokens(a_normalized, config)
    tokens_b = _significant_tokens(b_normalized, config)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def contains_personal_info(text: str) -> bool:
    return bool(_EMAIL_RE.search(text) or _PHONE_RE.search(text))


def is_cacheable(message: str, reply: str) -> bool:
    """Guards what's allowed into the cache -- see ChatQaCache's docstring."""
    if not message.strip() or not reply.strip():
        return False
    if contains_personal_info(message):
        return False
    if reply in _UNCACHEABLE_REPLIES:
        return False
    if reply.startswith('I am sorry, I could not process that right now'):
        return False
    return True


def find_cached_reply(message: str) -> ChatQaCache | None:
    normalized = _normalize(message)
    if not normalized:
        return None

    config = _config()
    best: ChatQaCache | None = None
    best_score = 0.0
    # Table stays small for a business this size (hundreds, not millions, of
    # distinct questions) -- a linear scan is simpler and easier to reason
    # about than standing up embeddings/vector search for that volume.
    for entry in ChatQaCache.query.all():
        score = _similarity(normalized, entry.question_normalized, config)
        if score > best_score:
            best, best_score = entry, score

    if best and best_score >= config['match_threshold']:
        logger.info('chat_cache_hit', score=round(best_score, 3), cache_id=best.id)
        return best
    return None


def store_reply(
    *,
    message: str,
    reply: str,
    action: str | None,
    action_payload: dict | None,
    model_name: str | None,
) -> None:
    if not is_cacheable(message, reply):
        return

    normalized = _normalize(message)
    # Don't grow the table with near-duplicates of something already stored --
    # find_cached_reply would have served this from cache if it were close
    # enough, so anything reaching here is either genuinely new or a near-miss
    # just under the threshold; either way a fresh row is fine.
    entry = ChatQaCache(
        question=message,
        question_normalized=normalized,
        reply=reply,
        action=action,
        action_payload=action_payload,
        model_name=model_name,
    )
    db.session.add(entry)
    logger.info('chat_cache_stored', question=normalized[:80])


def record_hit(entry: ChatQaCache) -> None:
    entry.hit_count += 1
