"""Keyword search (BM25) over the website knowledge built by site_crawler.

The whole site is a few hundred short chunks, so an in-memory index per
worker process is small (well under a few MB) and answers in microseconds
without an embeddings API. The index reloads when the crawler publishes a new
version; the version check hits the DB at most once a minute.
"""
from __future__ import annotations

import math
import re
import threading
import time
from array import array
from collections import Counter
from dataclasses import dataclass

from app.models.site_knowledge import SiteKnowledgeChunk
from app.models.site_setting import SiteSetting
from app.services import embedding_service

_VERSION_KEY = 'chat_knowledge_status'
THRESHOLDS_KEY = 'chat_semantic_thresholds'
_RRF_K = 60
_MEANING_POOL = 30
_VERSION_CHECK_SECONDS = 60
_K1, _B = 1.5, 0.75

STOPWORDS = frozenset('''
a about above after again against all am an and any are as at be because been before being below between both
but by can could did do does doing down during each few for from further had has have having he her here hers
herself him himself his how i if in into is it its itself just me more most my myself no nor not now of off on
once only or other our ours ourselves out over own same she should so some such than that the their theirs them
themselves then there these they this those through to too under until up very was we were what when where which
while who whom why will with would you your yours yourself yourselves hi hello hey please thanks thank ok okay
tell know want need like get got let also us one much many really im ive dont cant whats hows
offer offers offered offering provide provides providing supply supplies sell sells available
come comes coming came go goes going went make makes made use uses using used
take takes taking took want wants look looks looking keep keeps kept set sort check thing things
'''.split())

# A matched word this rare (in at most this share of sections) is specific to
# the site -- a place, product or service name like "Chelmsford" or
# "Hornetsecurity" -- so on its own it shows the question is about us.
_DISTINCTIVE_SHARE = 0.10


# Multi-word product names folded into one token before splitting, so the
# "office" in "Office 365" can't match a question about our office building
# (and vice versa).
_PHRASES = [
    (re.compile(r'\b(?:microsoft|office|ms)\s*-?\s*365\b|\bo365\b|\bm365\b'), ' microsoft365 '),
    (re.compile(r'\be-mail'), 'email'),
    (re.compile(r'\bwi-?fi\b'), 'wifi'),
    (re.compile(r'\bcyber\s+essentials\b'), ' cyberessentials '),
    # "IT" alone is dropped as the pronoun "it"; as a service it matters.
    (re.compile(r'\bit\s+(support|services?|helpdesk|department|team)\b'), r' it\1 '),
]

# The questions visitors ask most -- where, when, who, how to reach us, jobs
# -- share their key word ("based", "number", "hours") with dozens of
# unrelated sections, so keyword ranking alone can't pick the right one.
# Recognised explicitly and sent to the page that answers them.
_ROUTES: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"\bwhere\b.*\b(you|office|based|located|find)\b|\baddress\b|\bpost\s?code\b|\blocated\b|\blocation\b"
        r"|\bdirections\b|\b(opening|office|business) hours\b|\bopen(ing)? times\b|\bare you open\b"
        r"|\bwhat time do you\b|\bphone number\b|\btelephone\b|\bcontact (number|details|info)\b|\bemail address\b"
        r"|\b(contact|reach|get hold of|email|call|ring|phone|visit) you\b|\bspeak to (someone|somebody|a person|you)\b"
        r"|\bare you (in|near)\b|\bget in touch\b|\bwho (owns|runs|founded|started)\b|\bowner\b"
        r"|\bfounder\b|\bfounded\b|\bhow long have you been\b"
    ), '/contact'),
    (re.compile(
        r"\bhiring\b|\bvacanc|\bcareers?\b|\bjobs?\b|\bjob opening|\bwork for you\b|\bjoin (your|the) team\b"
        r"|\bapply for\b|\binternship|\bapprentice|\bgraduates?\b|\btrainees?\b|\bwork experience\b"
    ), '/careers'),
    (re.compile(r"\bwebsites?\b|\bweb design|\bwordpress\b|\be-?commerce\b|\bonline shop"), '/web-design'),
    (re.compile(
        r"\b(what|which) (it )?services\b|\bservices do you\b|\byour services\b|\blist of services\b"
        r"|\bwhat do you (do|offer|provide)\b"
    ), '/services'),
]


def route(query: str) -> str | None:
    """The page that answers a common, recognisable question, if any."""
    text = query.lower()
    return next((url for pattern, url in _ROUTES if pattern.search(text)), None)

# Tried in order; the first that leaves a stem of 3+ letters wins.
# "located"/"location"/"locations" -> "locat", "services"/"service" ->
# "servic", "migrating"/"migration" -> "migrat". Deliberately small -- no
# -er/-ers, which would merge unrelated words ("server" / "serve").
_SUFFIXES = ('ings', 'ing', 'ions', 'ion', 'ed', 'es', 'e', 's')


def stem(word: str) -> str:
    if len(word) <= 3 or word.isdigit() or word.endswith('ss'):
        return word
    if word.endswith(('ies', 'ied')) and len(word) > 4:
        return word[:-3] + 'y'            # companies -> company, applied -> apply
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    text = text.lower().replace("'", '')
    for pattern, replacement in _PHRASES:
        text = pattern.sub(replacement, text)
    words = re.findall(r"[a-z0-9][a-z0-9+#]*", text)
    return [stem(w) for w in words if w not in STOPWORDS and len(w) > 1]


@dataclass(frozen=True)
class Hit:
    score: float
    matched: frozenset[str]
    distinctive: bool   # matched at least one rare, site-specific word
    url: str
    title: str
    heading: str | None
    content: str
    source: str
    # Meaning similarity to the question (-1..1), or None when meaning
    # vectors weren't available for this search.
    similarity: float | None = None


class _Index:
    def __init__(self, rows: list[_Row]):
        self.rows = rows
        self.tfs: list[Counter] = []
        self.lengths: list[int] = []
        df: Counter = Counter()
        for r in rows:
            # Titles and headings are strong signals of what a chunk is about.
            tokens = tokenize(r.content) + tokenize(r.title) * 2 + tokenize(r.heading or '') * 2
            tf = Counter(tokens)
            self.tfs.append(tf)
            self.lengths.append(len(tokens) or 1)
            df.update(tf.keys())
        n = len(rows) or 1
        self.avg_len = sum(self.lengths) / n if self.lengths else 1
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        rare_limit = max(3, n * _DISTINCTIVE_SHARE)
        self.distinctive = frozenset(t for t, f in df.items() if f <= rare_limit)
        # Words used in page titles, headings and addresses -- where towns and
        # products get named ("IT Support in Witham", "Hornetsecurity backup").
        self.title_terms = frozenset(
            t for r in rows for t in tokenize(f"{r.title} {r.heading or ''} {r.url.replace('-', ' ').replace('/', ' ')}")
        )
        # Meaning search needs vectors for (nearly) the whole site; a partly
        # embedded index would rank embedded sections unfairly high.
        embedded = sum(1 for r in rows if r.vec is not None)
        self.has_vectors = bool(rows) and embedded >= 0.9 * len(rows)

    def search(self, query_tokens: list[str], limit: int, url: str | None = None,
               query_vec: array | None = None) -> list[Hit]:
        terms = [t for t in dict.fromkeys(query_tokens) if t in self.idf]
        use_vectors = query_vec is not None and self.has_vectors
        if url is not None and not terms and not use_vectors:
            # Routed question with no word in common with the page ("are you
            # hiring?"): the page's opening section is the answer.
            first = next((i for i, r in enumerate(self.rows) if r.url == url), None)
            return [] if first is None else [self._hit(0.0, frozenset(), first)]

        keyword: dict[int, tuple[float, frozenset]] = {}
        for i, tf in enumerate(self.tfs):
            if url is not None and self.rows[i].url != url:
                continue
            score, matched = 0.0, []
            for t in terms:
                f = tf.get(t)
                if f:
                    matched.append(t)
                    score += self.idf[t] * f * (_K1 + 1) / (f + _K1 * (1 - _B + _B * self.lengths[i] / self.avg_len))
            if score:
                keyword[i] = (score, frozenset(matched))

        if not use_vectors:
            ranked = sorted(keyword, key=lambda i: (keyword[i][0], -i), reverse=True)
            if url is not None and not ranked:
                return self.search([], limit, url)
            return [self._hit(keyword[i][0], keyword[i][1], i) for i in ranked[:limit]]

        # Hybrid: fuse the keyword ranking with the meaning ranking
        # (reciprocal rank fusion), so a section can win on either -- exact
        # names like "Chelmsford" via keywords, paraphrases ("how fast do you
        # turn up?") via meaning.
        similarity = {
            i: embedding_service.cosine(query_vec, r.vec)
            for i, r in enumerate(self.rows)
            if r.vec is not None and (url is None or r.url == url)
        }
        by_meaning = sorted(similarity, key=lambda i: similarity[i], reverse=True)[:_MEANING_POOL]
        by_keyword = sorted(keyword, key=lambda i: keyword[i][0], reverse=True)
        fused: dict[int, float] = {}
        for ranking in (by_keyword, by_meaning):
            for rank, i in enumerate(ranking):
                fused[i] = fused.get(i, 0.0) + 1.0 / (_RRF_K + rank + 1)
        ranked = sorted(fused, key=lambda i: (fused[i], -i), reverse=True)
        return [
            self._hit(fused[i], keyword.get(i, (0.0, frozenset()))[1], i, similarity.get(i))
            for i in ranked[:limit]
        ]

    def _hit(self, score: float, matched: frozenset, i: int, similarity: float | None = None) -> Hit:
        r = self.rows[i]
        return Hit(score, matched, bool(matched & self.distinctive), r.url, r.title, r.heading, r.content, r.source,
                   similarity)


_lock = threading.Lock()
_index: _Index | None = None
_index_version: str | None = None
_checked_at = 0.0


def _current_index() -> _Index:
    global _index, _index_version, _checked_at
    now = time.monotonic()
    if _index is not None and now - _checked_at < _VERSION_CHECK_SECONDS:
        return _index
    version = (SiteSetting.get(_VERSION_KEY) or {}).get('version')
    with _lock:
        _checked_at = now
        if _index is None or version != _index_version:
            rows = SiteKnowledgeChunk.query.all()
            # Detach plain values so the index never touches the session again.
            model = embedding_service.model_name()
            _index = _Index([_Row(r, model) for r in rows])
            _index_version = version
            _thresholds_cache.clear()
        return _index


class _Row:
    __slots__ = ('url', 'title', 'heading', 'content', 'source', 'vec')

    def __init__(self, r: SiteKnowledgeChunk, model: str):
        self.url, self.title, self.heading, self.content, self.source = r.url, r.title, r.heading, r.content, r.source
        # Vectors from a different embedding model aren't comparable.
        self.vec = embedding_service.from_bytes(r.embedding) if r.embedding_model == model else None


def _query_vector(query: str, index: _Index) -> array | None:
    # Only spend an API call when the index can use it.
    return embedding_service.embed_query(query) if index.has_vectors else None


def search(query: str, limit: int = 5, url: str | None = None) -> list[Hit]:
    """Best-matching sections, optionally only from one page. Uses meaning
    and keywords when meaning vectors are available, keywords alone
    otherwise (no key, API down, site not embedded yet)."""
    index = _current_index()
    return index.search(tokenize(query), limit, url, _query_vector(query, index))


def meaning_active() -> bool:
    return _current_index().has_vectors and embedding_service.is_available()


_thresholds_cache: dict = {}


def semantic_thresholds() -> dict | None:
    """Calibrated similarity cut-offs ({'relevant': x, 'answer': y}) for the
    current embedding model, or None until scripts/chatbot_eval.py
    --calibrate has been run. Without them, meaning only helps rank results;
    it never decides on its own that a question is on-topic or answerable."""
    now = time.monotonic()
    if _thresholds_cache and now - _thresholds_cache['at'] < _VERSION_CHECK_SECONDS:
        return _thresholds_cache['value']
    stored = SiteSetting.get(THRESHOLDS_KEY) or {}
    value = stored if stored.get('model') == embedding_service.model_name() else None
    _thresholds_cache.update(at=now, value=value)
    return value


def search_routed(query: str, limit: int = 5) -> list[Hit]:
    """Like search(), but a recognised common question (see _ROUTES) gets
    the sections of the page that answers it first."""
    hits = search(query, limit)
    target = route(query)
    if target is None:
        return hits
    routed = search(query, 2, url=target)
    seen = {(h.url, h.content) for h in routed}
    return (routed + [h for h in hits if (h.url, h.content) not in seen])[:limit]


_TOWN_PAGE_RE = re.compile(r'^/it-support-[a-z-]+$')
# Page-title fragments ("IT Support in London") match lots of questions and
# answer none of them.
_MIN_SECTION_CHARS = 40
_POOL = 30


def page_words(hit: Hit) -> set[str]:
    return set(tokenize(f"{hit.title} {hit.url.replace('-', ' ').replace('/', ' ')}"))


def relevant_sections(query: str, limit: int = 5) -> list[Hit]:
    """The sections to answer from -- for the AI's context and for quoting.
    Every town page says "IT support in <town>", so a general question would
    otherwise be answered from whichever town scored highest; town pages are
    kept only when the question names that town."""
    rare = set(tokenize(query)) & distinctive_terms()
    keep = [
        h for h in search_routed(query, _POOL)
        if len(h.content) >= _MIN_SECTION_CHARS and (not _TOWN_PAGE_RE.match(h.url) or rare & page_words(h))
    ]
    return keep[:limit]


_INTRO_MIN_CHARS = 150


def page_intro(url: str) -> Hit | None:
    """The first substantial section of a page -- its introduction."""
    index = _current_index()
    for i, r in enumerate(index.rows):
        if r.url == url and len(r.content) >= _INTRO_MIN_CHARS:
            return index._hit(0.0, frozenset(), i)
    return None


def names_in(query: str) -> set[str]:
    """Rare words in the question that look like names -- a town, product or
    brand -- which an answer has to contain literally. A rare *ordinary* word
    ("respond") isn't one: meaning matching should be free to answer it with
    "response time". Name-like = rare on the site, and either used in a page
    title/heading/address or capitalised by the visitor."""
    index = _current_index()
    capitalised = {stem(w.lower()) for w in re.findall(r"\b[A-Z][a-zA-Z0-9]+", query)[1:]}
    rare = set(tokenize(query)) & index.distinctive
    return {t for t in rare if t in index.title_terms or t in capitalised}


def distinctive_terms() -> frozenset[str]:
    return _current_index().distinctive


def vocabulary_share(query: str) -> float:
    """Share of the query's meaningful words that appear anywhere on the site."""
    tokens = set(tokenize(query))
    if not tokens:
        return 0.0
    idf = _current_index().idf
    return sum(1 for t in tokens if t in idf) / len(tokens)


def chunk_count() -> int:
    return len(_current_index().rows)


def invalidate() -> None:
    global _checked_at
    _checked_at = 0.0
