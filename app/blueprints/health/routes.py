from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, current_app, render_template_string
from sqlalchemy import text

from app.extensions import db
from app.utils.response import envelope

blp = Blueprint('health', __name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=1)
def _git_info() -> dict:
    """Read the deployed commit + a short recent-changes list straight from
    git, cached for the life of the process — this is what actually answers
    "did my deploy work" (commit hash) and "what changed" (recent log),
    without needing a separate build/version-stamping step."""
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ['git', *args], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            return ''

    commit = run('rev-parse', '--short', 'HEAD') or 'unknown'
    commit_date = run('log', '-1', '--format=%cd', '--date=format:%d %b %Y, %H:%M')
    log_lines = run('log', '-5', '--format=%h|%s|%cd', '--date=format:%d %b')
    changes = []
    for line in log_lines.splitlines():
        parts = line.split('|', 2)
        if len(parts) == 3:
            changes.append({'hash': parts[0], 'message': parts[1], 'date': parts[2]})
    return {'commit': commit, 'commit_date': commit_date, 'changes': changes}


_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Internet Assist API</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 2rem;
    }
    .card {
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 16px;
      padding: 2.5rem 3rem;
      max-width: 560px;
      width: 100%;
      box-shadow: 0 25px 50px -12px rgba(0,0,0,.5);
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: .4rem;
      color: #fff;
      font-size: .7rem;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
      border-radius: 999px;
      padding: .25rem .75rem;
      margin-bottom: 1.25rem;
    }
    .badge.ok { background: #10b981; }
    .badge.fail { background: #ef4444; }
    .dot { width: 7px; height: 7px; background: #fff; border-radius: 50%; animation: pulse 2s infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
    h1 { font-size: 1.75rem; font-weight: 700; color: #f8fafc; line-height: 1.25; }
    p  { color: #94a3b8; margin-top: .75rem; line-height: 1.6; font-size: .95rem; }
    .links { margin-top: 1.5rem; display: flex; flex-wrap: wrap; gap: .75rem; }
    .btn {
      display: inline-flex; align-items: center; gap: .4rem;
      padding: .6rem 1.25rem; border-radius: 8px; font-size: .875rem;
      font-weight: 600; text-decoration: none; transition: opacity .15s;
    }
    .btn:hover { opacity: .85; }
    .btn-primary { background: #2563eb; color: #fff; }
    .btn-outline { background: transparent; color: #94a3b8; border: 1px solid #334155; }
    .meta { margin-top: 2rem; display: grid; grid-template-columns: auto 1fr; gap: .4rem 1rem; font-size: .85rem; }
    .meta dt { color: #64748b; font-weight: 600; }
    .meta dd { color: #e2e8f0; font-family: monospace; }
    .changes { margin-top: 2rem; }
    .changes h2 { font-size: .8rem; font-weight: 600; text-transform: uppercase;
                    letter-spacing: .08em; color: #64748b; margin-bottom: .75rem; }
    .change { display: flex; align-items: baseline; gap: .6rem;
                padding: .45rem .75rem; border-radius: 6px; background: #0f172a;
                margin-bottom: .35rem; font-size: .8rem; }
    .change .hash { font-family: monospace; color: #a78bfa; flex-shrink: 0; }
    .change .msg { color: #e2e8f0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
    .change .date { color: #64748b; flex-shrink: 0; font-size: .75rem; }
  </style>
</head>
<body>
  <div class="card">
    <div class="badge {{ 'ok' if db_ok else 'fail' }}"><div class="dot"></div> {{ 'Live — DB connected' if db_ok else 'DB connection failed' }}</div>
    <h1>Internet Assist API</h1>
    <p>Flask REST backend powering the Internet Assist platform.</p>

    <div class="links">
      {% if show_docs %}<a class="btn btn-primary" href="/docs">Swagger Docs</a>{% endif %}
      <a class="btn btn-outline" href="/healthz">Health Check</a>
      <a class="btn btn-outline" href="/readyz">Readiness</a>
    </div>

    <dl class="meta">
      <dt>Version</dt><dd>{{ commit }}</dd>
      <dt>Deployed</dt><dd>{{ commit_date or 'unknown' }}</dd>
      <dt>Environment</dt><dd>{{ env }}</dd>
    </dl>

    {% if changes %}
    <div class="changes">
      <h2>Recent changes</h2>
      {% for c in changes %}
      <div class="change"><span class="hash">{{ c.hash }}</span><span class="msg">{{ c.message }}</span><span class="date">{{ c.date }}</span></div>
      {% endfor %}
    </div>
    {% endif %}
  </div>
</body>
</html>"""


@blp.route('/')
def index():
    env = current_app.config.get('APP_ENV', 'development')
    git_info = _git_info()

    try:
        db.session.execute(text('SELECT 1'))
        db_ok = True
    except Exception:
        db_ok = False

    return render_template_string(
        _INDEX_HTML,
        env=env,
        db_ok=db_ok,
        commit=git_info['commit'],
        commit_date=git_info['commit_date'],
        changes=git_info['changes'],
        show_docs=bool(current_app.config.get('OPENAPI_SWAGGER_UI_PATH')),
    )


@blp.route('/healthz')
def healthz():
    return envelope(data={'status': 'ok'}, status=200)


@blp.route('/readyz')
def readyz():
    db.session.execute(text('SELECT 1'))
    # Don't expose environment name in production responses
    return envelope(data={'status': 'ready'}, status=200)
