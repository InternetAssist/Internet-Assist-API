"""Builds the chatbot's knowledge base from the public website.

The frontend build prerenders every marketing route to static HTML with the
page's real copy inside <div id="root"> (see prerenderHeads in the frontend's
vite.config.ts), so a plain HTTP fetch sees the same text a visitor does --
no headless browser needed. Pages come from the site's sitemap.xml; published
blog posts and open job postings are read straight from the database.

Run automatically in the background when the knowledge is older than
CHAT_KNOWLEDGE_MAX_AGE_HOURS, from Admin -> AI Assistant -> "Re-index
website", or manually with `flask chatbot reindex`.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests
from flask import current_app

from app.extensions import db
from app.logging import logger
from app.models.blog_post import BlogPost
from app.models.chat_qa_cache import ChatQaCache
from app.models.job_posting import JobPosting
from app.models.site_knowledge import SiteKnowledgeChunk
from app.models.site_setting import SiteSetting
from app.services import embedding_service

STATUS_KEY = 'chat_knowledge_status'
_CRAWL_LOCK_KEY = 'chat_knowledge_crawl_started'

_MAX_PAGES = 200
_MAX_PAGE_BYTES = 3 * 1024 * 1024
_FETCH_TIMEOUT = 10
_POLITE_DELAY_SECONDS = 0.1
_CHUNK_CHARS = 1000
_USER_AGENT = 'InternetAssistChatbotIndexer/1.0'

# Blog post URLs in the sitemap serve the SPA shell (no prerendered copy);
# posts are indexed from the database instead.
_SKIP_PATH_PREFIXES = ('/blog/', '/admin', '/auth')

_process_lock = threading.Lock()


# ── HTML → sections ───────────────────────────────────────────────────────────

_VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
_SKIP_TAGS = {'script', 'style', 'noscript', 'svg', 'template', 'iframe', 'button', 'form', 'nav', 'footer', 'head'}
_HEADING_TAGS = {'h1', 'h2', 'h3'}
_BLOCK_TAGS = {'p', 'li', 'div', 'section', 'main', 'article', 'h4', 'h5', 'h6', 'td', 'th', 'dd', 'dt', 'tr', 'ul', 'ol'}


class _PageExtractor(HTMLParser):
    """Collects (heading, text) sections from inside #root, skipping the boot
    loader, scripts, navigation and other chrome."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ''
        self.description = ''
        self.sections: list[tuple[str | None, str]] = []
        self._depth = 0
        self._skip_depth: int | None = None
        self._root_depth: int | None = None
        self._in_title = False
        self._heading: str | None = None
        self._heading_parts: list[str] | None = None
        self._parts: list[str] = []

    def _flush(self):
        text = _clean('\n'.join(''.join(self._parts).split('\n')))
        if text:
            self.sections.append((self._heading, text))
        self._parts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta' and (a.get('name') or '').lower() == 'description':
            self.description = a.get('content') or ''
        if tag == 'title':
            self._in_title = True
        if tag in _VOID_TAGS:
            if tag == 'br':
                self._parts.append('\n')
            return
        self._depth += 1
        if self._skip_depth is None and (tag in _SKIP_TAGS or a.get('id') == 'boot-loader' or a.get('aria-hidden') == 'true'):
            self._skip_depth = self._depth
        if a.get('id') == 'root' and self._root_depth is None:
            self._root_depth = self._depth
        if self._skip_depth is not None or self._root_depth is None:
            return
        if tag in _HEADING_TAGS:
            self._flush()
            self._heading_parts = []
        elif tag in _BLOCK_TAGS:
            self._parts.append('\n')

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        if tag == 'title':
            self._in_title = False
        if tag in _HEADING_TAGS and self._heading_parts is not None:
            self._heading = _clean(''.join(self._heading_parts))[:255] or None
            self._heading_parts = None
        if self._skip_depth == self._depth:
            self._skip_depth = None
        if self._root_depth == self._depth:
            self._flush()
            self._root_depth = None
        self._depth = max(self._depth - 1, 0)

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip_depth is not None or self._root_depth is None:
            return
        if self._heading_parts is not None:
            self._heading_parts.append(data)
        else:
            self._parts.append(data)


def _clean(text: str) -> str:
    lines = [' '.join(line.split()) for line in text.split('\n')]
    return '\n'.join(line for line in lines if line)


def _split(text: str, limit: int = _CHUNK_CHARS) -> list[str]:
    """Split text into <= limit character pieces on line, then sentence,
    boundaries."""
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    current = ''
    for unit in re.split(r'(?<=[.!?])\s+|\n', text):
        unit = unit.strip()
        if not unit:
            continue
        while len(unit) > limit:
            pieces.append(unit[:limit])
            unit = unit[limit:]
        if current and len(current) + 1 + len(unit) > limit:
            pieces.append(current)
            current = unit
        else:
            current = f'{current} {unit}'.strip()
    if current:
        pieces.append(current)
    return pieces


def _page_chunks(path: str, html: str) -> list[dict]:
    parser = _PageExtractor()
    parser.feed(html)
    parser.close()
    title = _clean(parser.title).split(' | ')[0][:255] or path
    sections = parser.sections or ([(None, _clean(parser.description))] if parser.description else [])

    chunks: list[dict] = []
    for heading, text in sections:
        for piece in _split(text):
            # Merge tiny fragments into the previous chunk of the same page.
            if chunks and len(piece) < 200 and len(chunks[-1]['content']) + len(piece) < _CHUNK_CHARS:
                chunks[-1]['content'] += '\n' + piece
                continue
            chunks.append({'source': 'page', 'url': path, 'title': title, 'heading': heading, 'content': piece})
    return chunks


# ── Fetching ──────────────────────────────────────────────────────────────────

def _allowed_hosts(site_url: str) -> set[str]:
    host = (urlparse(site_url).hostname or '').lower()
    bare = host[4:] if host.startswith('www.') else host
    return {bare, f'www.{bare}'}


def _fetch(url: str, allowed_hosts: set[str]) -> str | None:
    try:
        with requests.get(url, timeout=_FETCH_TIMEOUT, stream=True, headers={'User-Agent': _USER_AGENT}) as resp:
            if resp.status_code != 200:
                return None
            if (urlparse(resp.url).hostname or '').lower() not in allowed_hosts:
                return None   # redirected off-site
            body = bytearray()
            for part in resp.iter_content(64 * 1024):
                body.extend(part)
                if len(body) > _MAX_PAGE_BYTES:
                    return None
            # requests assumes ISO-8859-1 when the server sends no charset,
            # which garbles every em dash and curly quote. The site is UTF-8.
            declared = 'charset=' in resp.headers.get('Content-Type', '').lower()
            return bytes(body).decode(resp.encoding if declared else 'utf-8', errors='replace')
    except requests.RequestException:
        return None


def _sitemap_urls(site_url: str, allowed_hosts: set[str]) -> list[str]:
    xml = _fetch(f'{site_url}/sitemap.xml', allowed_hosts) or ''
    urls = [u.strip() for u in re.findall(r'<loc>\s*([^<]+?)\s*</loc>', xml)]
    urls = [urljoin(site_url + '/', u) for u in urls]
    urls = [u for u in urls if (urlparse(u).hostname or '').lower() in allowed_hosts]
    if f'{site_url}/' not in urls:
        urls.insert(0, f'{site_url}/')
    seen, ordered = set(), []
    for u in urls:
        path = urlparse(u).path or '/'
        if path in seen or path.startswith(_SKIP_PATH_PREFIXES):
            continue
        seen.add(path)
        ordered.append(u)
    return ordered[:_MAX_PAGES]


# ── Database content ──────────────────────────────────────────────────────────

_MD_NOISE = re.compile(r'!\[[^\]]*\]\([^)]*\)|[#*_>`~|]+')
_MD_LINK = re.compile(r'\[([^\]]+)\]\([^)]*\)')


def _facts_chunk() -> dict:
    """The fixed company facts the AI prompt uses, also as a searchable
    section -- "where are you based?" or "what are your hours?" should find
    the address and hours, not a page that happens to say "UK-based"."""
    from app.blueprints.chat.ai_gateway import _COMPANY_FACTS

    lines = [line.strip() for line in _COMPANY_FACTS.strip().splitlines() if line.strip()]
    address = next((line.split(':', 1)[1].strip() for line in lines if line.startswith('Address:')), 'Maldon, Essex')
    return {
        'source': 'facts',
        'url': '/contact',
        'title': 'Contact Internet Assist',
        'heading': 'Our address, opening hours and contact details',
        'content': f'Our office is located at {address}. Contact details and opening hours:\n' + '\n'.join(lines),
    }


def _db_chunks() -> list[dict]:
    chunks: list[dict] = [_facts_chunk()]
    for post in BlogPost.query.filter_by(status='published').all():
        body = _MD_NOISE.sub(' ', _MD_LINK.sub(r'\1', post.body or ''))
        text = _clean('\n'.join(filter(None, [post.excerpt, body])))
        for piece in _split(text):
            chunks.append({'source': 'blog', 'url': f'/blog/{post.slug}', 'title': post.title[:255],
                           'heading': None, 'content': piece})

    for job in JobPosting.query.filter_by(status='active').all():
        lines = [
            f'Job opening: {job.title}',
            ' · '.join(filter(None, [job.team, job.location, job.employment_type])),
            job.summary or '',
        ]
        if job.responsibilities:
            lines.append('Responsibilities: ' + '; '.join(map(str, job.responsibilities)))
        if job.requirements:
            lines.append('Requirements: ' + '; '.join(map(str, job.requirements)))
        for piece in _split(_clean('\n'.join(lines))):
            chunks.append({'source': 'job', 'url': '/careers', 'title': f'Careers — {job.title}'[:255],
                           'heading': job.title[:255], 'content': piece})
    return chunks


# ── Meaning vectors ───────────────────────────────────────────────────────────

def _vector_key(chunk: dict, model: str) -> str:
    raw = f"{model}\n{chunk['title']}\n{chunk.get('heading') or ''}\n{chunk['content']}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _attach_embeddings(chunks: list[dict]) -> int:
    """Give each chunk a meaning vector, reusing the previous crawl's vector
    when a section's text hasn't changed -- a daily re-index of an unchanged
    site costs no embedding calls at all. If the API is unavailable the
    chunks are stored without vectors (keyword search still works) and the
    next crawl tries again. Returns how many chunks have a vector."""
    model = embedding_service.model_name()
    previous = {
        _vector_key({'title': r.title, 'heading': r.heading, 'content': r.content}, model): r.embedding
        for r in SiteKnowledgeChunk.query.filter(
            SiteKnowledgeChunk.embedding.isnot(None), SiteKnowledgeChunk.embedding_model == model)
    }
    missing = []
    for c in chunks:
        blob = previous.get(_vector_key(c, model))
        c['embedding'], c['embedding_model'] = (blob, model) if blob else (None, None)
        if not blob:
            missing.append(c)

    if missing:
        texts = [(c['title'], f"{c['heading']}\n{c['content']}" if c.get('heading') else c['content']) for c in missing]
        vectors = embedding_service.embed_documents(texts)
        if vectors is not None:
            for c, v in zip(missing, vectors):
                c['embedding'], c['embedding_model'] = embedding_service.to_bytes(v), model
    logger.info('chat_knowledge_embedded', reused=len(chunks) - len(missing), new=len(missing))
    return sum(1 for c in chunks if c['embedding'])


# ── Rebuild ───────────────────────────────────────────────────────────────────

def rebuild_knowledge() -> dict:
    """Crawl the site and replace the knowledge base. Keeps the existing
    knowledge if the site couldn't be reached at all. Returns the new status."""
    if not _process_lock.acquire(blocking=False):
        return {'skipped': 'already running in this process'}
    started = time.monotonic()
    try:
        site_url = current_app.config['SITE_URL']
        allowed = _allowed_hosts(site_url)
        urls = _sitemap_urls(site_url, allowed)

        chunks: list[dict] = []
        pages_ok, errors = 0, []
        for url in urls:
            html = _fetch(url, allowed)
            if html is None:
                errors.append(url)
                continue
            pages_ok += 1
            chunks.extend(_page_chunks(urlparse(url).path or '/', html))
            time.sleep(_POLITE_DELAY_SECONDS)

        if pages_ok == 0:
            logger.error('chat_knowledge_crawl_failed', site=site_url, errors=len(errors))
            status = dict(SiteSetting.get(STATUS_KEY) or {})
            status['last_error'] = f'Could not reach {site_url} -- kept the previous knowledge.'
            status['last_attempt_at'] = datetime.now(timezone.utc).isoformat()
            SiteSetting.upsert(STATUS_KEY, status)
            return status

        chunks.extend(_db_chunks())

        # The service list and "areas we cover" block repeat on every page --
        # keep each distinct piece of text once.
        seen, unique = set(), []
        for c in chunks:
            digest = hashlib.sha1(c['content'].lower().encode()).hexdigest()
            if digest not in seen:
                seen.add(digest)
                unique.append(c)

        embedded = _attach_embeddings(unique)

        SiteKnowledgeChunk.query.delete()
        db.session.bulk_insert_mappings(SiteKnowledgeChunk, unique)
        # Cached answers may quote pages that just changed.
        ChatQaCache.query.delete()
        db.session.commit()

        status = {
            'version': uuid.uuid4().hex,
            'crawled_at': datetime.now(timezone.utc).isoformat(),
            'last_attempt_at': datetime.now(timezone.utc).isoformat(),
            'site_url': site_url,
            'pages': pages_ok,
            'chunks': len(unique),
            # Sections with a meaning vector; the rest use keyword search.
            'embedded_chunks': embedded,
            'embedding_model': embedding_service.model_name() if embedded else None,
            'failed_urls': errors[:20],
            'duration_s': round(time.monotonic() - started, 1),
            'last_error': None,
        }
        SiteSetting.upsert(STATUS_KEY, status)
        logger.info('chat_knowledge_rebuilt', pages=pages_ok, chunks=len(unique), failed=len(errors))
        return status
    except Exception:
        db.session.rollback()
        logger.exception('chat_knowledge_rebuild_error')
        raise
    finally:
        _process_lock.release()


def knowledge_status() -> dict:
    return SiteSetting.get(STATUS_KEY) or {}


def refresh_if_stale() -> None:
    """Kick off a background re-index when the knowledge is missing or older
    than CHAT_KNOWLEDGE_MAX_AGE_HOURS. Never blocks the caller. Several worker
    processes may call this at once -- the DB timestamp stops them all
    crawling together."""
    from app.services import background

    now = datetime.now(timezone.utc)
    status = knowledge_status()
    attempted = status.get('last_attempt_at') or status.get('crawled_at')
    max_age = timedelta(hours=current_app.config['CHAT_KNOWLEDGE_MAX_AGE_HOURS'])
    # Sections stored without vectors (embedding API was down or the key was
    # added later) are retried hourly rather than waiting a whole day.
    if status.get('chunks') and status.get('embedded_chunks', 0) < status['chunks'] \
            and embedding_service.is_available():
        max_age = min(max_age, timedelta(hours=1))
    if attempted and now - datetime.fromisoformat(attempted) < max_age:
        return
    started = SiteSetting.get(_CRAWL_LOCK_KEY)
    if started and now - datetime.fromisoformat(started) < timedelta(minutes=20):
        return
    SiteSetting.upsert(_CRAWL_LOCK_KEY, now.isoformat())
    background.submit(rebuild_knowledge)
