# Internet Assist — Backend API (B1)

Flask REST API backing the public website and its staff admin panel. Not
covered here: the C#/.NET ticket platform (`Ticket/`) — that's a separate
service this API talks to over HTTP, documented in its own repo.

## Tech stack

| Layer | Choice |
|---|---|
| Framework | Flask 3 + flask-smorest (OpenAPI-driven blueprints) |
| ORM / migrations | SQLAlchemy 2 + Flask-Migrate (Alembic) |
| Database | MariaDB (MySQL-compatible), via `PyMySQL` |
| Auth | Azure AD (Microsoft Entra) OAuth via `msal`, JWT session cookie via `Flask-JWT-Extended` |
| Rate limiting | `Flask-Limiter` (in-memory by default; `REDIS_URL` for multi-worker) |
| Outbound email | Microsoft Graph API (delegated to the same Azure AD app used for login) |
| WSGI server (dev) | `waitress` — swap for `gunicorn` on Linux in production |
| Logging | `structlog`, JSON lines |

## Architecture

```
app/
  __init__.py           # app factory: config, extensions, blueprints, security headers
  config.py              # imports project_settings.py (repo root)
  extensions.py           # db, migrate, jwt, cors, limiter, api (flask-smorest) singletons
  errors.py               # error handler registration → app/utils/response.py envelope format
  logging.py              # structlog setup

  blueprints/
    admin/routes.py       # everything under /admin/* — staff-only, roles_required('admin')
    public/               # unauthenticated form submissions
      contact_routes.py     POST /contact
      quote_routes.py       POST /quotes
      job_routes.py         POST /job-applications
      job_posting_routes.py GET  /job-postings
      remote_support_routes.py POST /remote-support-request
    chat/                  # AI chatbot widget backend
      routes.py            POST /chat
      service.py            session/message persistence, ticket handoff
      ai_gateway.py          Gemini API wrapper
    settings/routes.py     # feature toggles: season, chatbot, enquiry-forwarding, AI key
    analytics/routes.py    # page-view tracking + admin summary
    health/routes.py       # /, /healthz, /readyz
    media/routes.py        # GET /media/projects/<file_name> (decrypts on the fly)

  models/                 # one file per SQLAlchemy model (see Data model section)
  schemas/                # marshmallow request/response schemas (admin.py, public.py, base.py)
  services/               # business logic + external integrations (see Services section)
  utils/
    decorators.py          # roles_required() — resolves the signed-in admin from JWT claims
    response.py             # envelope()/error_envelope() — the {data, error, meta} response shape
```

Every JSON response follows the same envelope:

```json
{ "data": <payload or null>, "error": {"code", "message", "details"} | null, "meta": {"page", "page_size", "total", "has_more"} | null }
```

## Authentication & authorization

**There is no local users/roles table.** Azure AD (Entra ID) is the sole
identity source — this was a deliberate design choice: "enterprise" SSO
where authorization state lives entirely in the JWT issued at login, not in
a database the app has to keep in sync.

Flow (`app/blueprints/admin/routes.py`, `app/services/ms_auth_service.py`):

1. **`GET /admin/login/microsoft`** — builds an MSAL auth-code+PKCE flow,
   stashes the flow state (state/nonce/PKCE verifier) in Flask's signed
   `session` cookie, redirects the browser to Microsoft.
2. User authenticates with Microsoft.
3. **`GET /admin/login/microsoft/callback`** — completes the token exchange,
   reads `oid`/`email`/`name` off the ID token. Then, using a *separate*
   app-only Graph token (client-credentials flow, same Azure AD app
   registration — "Tech Support" — already used for sending email), checks
   whether that `oid` holds the **`ia-support-admin`** custom App Role on
   the app's service principal (`GET /servicePrincipals/{spId}/appRoleAssignedTo`).
   - Not authorized → redirect to `{FRONTEND_URL}/auth?error=forbidden`.
   - Authorized → also fetches `jobTitle`/`department` via Graph, issues a
     JWT (`create_access_token`) with `additional_claims = {full_name, roles: ['admin'], oid, job_title, department}`,
     sets it as an httpOnly cookie, redirects to `{FRONTEND_URL}/admin`.
4. Every protected admin route is decorated `@roles_required('admin')`
   (`app/utils/decorators.py`), which reads the claims straight off the JWT
   into a `CurrentUser` dataclass (`g.current_user`) — no DB lookup.
5. **`GET /admin/me`** — returns the current claims as JSON (used by the
   frontend to know who's signed in after the OAuth redirect).
6. **`GET /admin/me/photo`** — proxies the user's Azure AD profile photo
   (app-only Graph call to `/users/{oid}/photos/64x64/$value`); 404 if the
   user has none set.
7. **`POST /admin/logout`** — blacklists the JWT's `jti` (`token_blacklist`
   table) and clears the cookie.

**Granting/revoking access** happens entirely in Azure Portal → Entra ID →
Enterprise Applications → "Tech Support" → Users and groups → assign/remove
the `ia-support-admin` role. No app deploy needed. **Caveat:** revoking the
role doesn't invalidate an already-issued JWT — the user keeps admin access
until their token expires (`JWT_ACCESS_TOKEN_EXPIRES`, 8 hours) or they log
out. There is no instant-revoke mechanism by design (accepted trade-off,
not a bug).

**Cookie behaviour**: `JWT_COOKIE_SAMESITE` is `Lax` in dev, `None` (with
`Secure`) in production — production serves the frontend from more than one
registrable domain (`ia.uk` and the `ia-webdesign.com` alias), which makes
the JWT cookie genuinely cross-*site* for one of them. CSRF protection
comes from the CORS origin allowlist plus every state-changing route
requiring a JSON body (a plain cross-site HTML form can't send `application/json`).

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `APP_ENV` | prod | `development` \| `testing` \| `production`. Also drives `JWT_COOKIE_SECURE`/`SAMESITE`, and forces `https://` in generated media URLs when `production`. |
| `SECRET_KEY` | prod | Flask session signing (used for the OAuth flow-state cookie). |
| `JWT_SECRET_KEY` | prod | JWT signing secret. |
| `DATABASE_URL` | yes | SQLAlchemy URL, e.g. `mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4`. |
| `CORS_ORIGINS` | yes | Comma-separated allowlist, e.g. `https://ia.uk,https://ticketing.ia.uk`. |
| `FRONTEND_URL` | yes | Where OAuth redirects land (`/admin` or `/auth?error=...`). |
| `TICKET_API_URL` | no | Base URL of the external ticket platform (Contact/Quote submissions create a ticket there). Blank = skipped gracefully. |
| `GRAPH_TENANT_ID` / `GRAPH_CLIENT_ID` / `GRAPH_CLIENT_SECRET` | yes | Azure AD app registration ("Tech Support") — used for sending email via Graph. |
| `GRAPH_SENDER` | yes | Mailbox address emails are sent from. |
| `NOTIFY_EMAIL_1` / `NOTIFY_EMAIL_2` | no | Enquiry inbox(es) that receive Contact/Quote/Job Application notifications. |
| `MS_AUTH_TENANT_ID` / `MS_AUTH_CLIENT_ID` / `MS_AUTH_CLIENT_SECRET` | no | Defaults to the `GRAPH_*` values above — override only if login should use a different app registration. |
| `MS_AUTH_SP_ID` | no | Service principal ID of the "Tech Support" app (default baked in). |
| `MS_AUTH_ADMIN_ROLE_ID` | no | App Role ID for `ia-support-admin` (default baked in). |
| `AI_PROVIDER` / `AI_MODEL_NAME` | no | Chatbot LLM provider/model (currently `gemini`). |
| `AI_API_KEY` | no | Fallback Gemini key; can also be set/rotated from Admin → Settings (stored encrypted in `site_settings` via `ai_config_service`). |
| `MEDIA_ENCRYPTION_KEY` | prod | Fernet key — project images/CVs/documents are encrypted at rest. |
| `MEDIA_UPLOAD_DIR` | no | Where encrypted media files live (defaults to a temp dir). |
| `SITE_SETTINGS_DIR` | no | Where the flat-file settings JSON (season/chatbot/enquiry-forwarding) lives. |
| `PUBLIC_CONTACT_EMAIL` / `PUBLIC_CONTACT_PHONE` | no | Shown in chatbot/emails. |
| `REDIS_URL` | no | Rate-limiter storage backend for multi-worker deployments (defaults to in-memory, which is per-process only). |
| `APPINSIGHTS_CONNECTION_STRING` | no | Azure App Insights, if used. |

Generate a `SECRET_KEY`/`JWT_SECRET_KEY`: `python3 -c "import secrets; print(secrets.token_hex(32))"`
Generate a `MEDIA_ENCRYPTION_KEY`: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

`ProductionConfig.validate()` fails fast at startup if `SECRET_KEY`,
`JWT_SECRET_KEY`, `MEDIA_ENCRYPTION_KEY`, or `DATABASE_URL` are missing/insecure.

## Data model

| Table | Purpose | Notable fields |
|---|---|---|
| `contacts` | Contact form submissions | `status` (new/in_progress/closed), `ticket_id`/`ticket_ref` (external ticket link) |
| `quotes` | Quote requests | `services` (JSON array), `status` (pending/in_progress/closed) |
| `job_applications` | Careers applications | `cv_blob_url` (encrypted file name), `status` (new/reviewed/shortlisted/rejected) |
| `job_postings` | Open roles shown publicly | `responsibilities`/`requirements` (JSON arrays), `status` (active/closed) |
| `projects` | "Client stories" portfolio | `service_type`, `tags` (JSON), `image_file_id` (encrypted local image) or `image_url` (external), `status` (draft/published) |
| `chat_sessions` / `chat_messages` | Chatbot widget conversations | `ticket_flow_state`/`ticket_flow_data` (JSON) drive the in-chat ticket-creation flow |
| `page_views` | Analytics | `device_type`, `browser`, `os`, `ip_hash` (not raw IP) |
| `audit_logs` | Every admin action | `actor_user_id` (**email string**, not a FK — no users table), `action`, `entity`, `diff` (JSON) |
| `site_settings` | Key/value JSON store | Currently only used by `ai_config_service` for the encrypted Gemini key |
| `token_blacklist` | Revoked JWTs | `jti` + `expires_at`, checked on every request via `jwt.token_in_blocklist_loader` |

Feature toggles (`season`, `chatbot`, `enquiry_forwarding`) are **not** in
the database — they live in a flat JSON file (`app/services/file_settings.py`),
deliberately simple since they're rarely-changed, non-critical settings.

## API reference

All routes are prefixed at the app root (no `/api/v1` prefix despite
`API_PREFIX` being configured — flask-smorest doesn't apply it automatically
without an explicit blueprint `url_prefix`, and none is set).

### Public (no auth)
| Method | Path | Notes |
|---|---|---|
| POST | `/contact` | Saves a Contact, creates an external ticket (best-effort), emails the enquiry inbox if the toggle is on |
| POST | `/quotes` | Same pattern as Contact |
| POST | `/job-applications` | `multipart/form-data` (CV upload), emails HR with the CV attached + sends an applicant confirmation |
| GET | `/job-postings` | Active postings only |
| POST | `/remote-support-request` | Creates an external ticket |
| POST | `/chat` | Chatbot widget turn — see Services → AI Gateway below |
| GET | `/job-postings`, `/projects` | Public listings (the latter lives in `admin/routes.py` but is unauthenticated) |
| POST | `/analytics/track` | Page-view beacon |
| GET | `/`, `/healthz`, `/readyz` | Health checks |
| GET | `/settings/season`, `/settings/chatbot` | Public reads of the two visitor-facing toggles |
| GET | `/media/projects/<file_name>` | Decrypts and streams a project image |

### Admin (`@roles_required('admin')` unless noted)
| Method | Path | Notes |
|---|---|---|
| GET | `/admin/login/microsoft` | Starts OAuth (no auth required, obviously) |
| GET | `/admin/login/microsoft/callback` | OAuth callback (no auth required) |
| GET | `/admin/me` | Current session claims |
| GET | `/admin/me/photo` | Proxied Azure AD profile photo |
| POST | `/admin/logout` | Blacklists JWT, clears cookie |
| GET/PATCH/DELETE | `/admin/contacts[/​<id>]` | List/update-status/archive |
| GET/PATCH/DELETE | `/admin/quotes[/​<id>]` | Same pattern |
| GET/PATCH/GET/DELETE | `/admin/jobs[/​<id>]`, `/admin/jobs/<id>/cv` | Applications list/update/detail/CV download |
| GET/POST/PATCH/DELETE | `/admin/job-postings[/​<id>]` | Manage open roles |
| GET/POST/PATCH/DELETE | `/admin/projects[/​<id>]`, `/admin/projects/<id>/image` | Manage portfolio; image upload endpoint |
| GET | `/admin/stats` | Dashboard counts + status breakdowns |
| GET | `/admin/audit-logs[/​<id>]` | Paginated, filterable by entity/action/actor (partial email match) |
| GET | `/admin/analytics` | Page-view summary |
| GET/PATCH | `/admin/settings/season`, `/admin/settings/chatbot`, `/admin/settings/enquiry-forwarding` | Feature toggles |
| GET/PATCH/DELETE | `/admin/settings/ai` | Gemini API key (never echoed back once saved) |

Every list/mutate admin route calls `log_audit_action(...)` — nothing
happens silently.

## Services (`app/services/`)

- **`ms_auth_service.py`** — MSAL wrappers (`build_auth_flow`,
  `complete_auth_flow`), app-only Graph token (`_graph_app_token`), role
  check (`has_admin_app_role`), profile lookups (`get_profile_details`,
  `get_profile_photo`).
- **`email_service.py`** — all outbound mail via Graph `sendMail`.
  `send_ticket`/`send_ticket_with_attachments` notify the enquiry inbox for
  new submissions (gated by the `enquiry_forwarding` toggle);
  `send_confirmation` emails the submitter; `send_job_status_update` emails
  applicants when their status changes.
- **`ticket_service.py`** — `create_ticket()` posts to the external
  ticketing platform (`TICKET_API_URL`); failures are logged and swallowed
  (a down ticket platform shouldn't block a form submission).
- **`media_service.py`** — Fernet-encrypts/decrypts uploaded images and
  documents (CVs) at rest on local disk.
- **`crypto_service.py`** — shared Fernet helper used by `ai_config_service`
  to store the Gemini key encrypted rather than plaintext.
- **`ai_config_service.py`** — get/set/clear the Gemini API key in
  `site_settings`.
- **`file_settings.py`** — flat-file JSON store for `season`/`chatbot`/`enquiry_forwarding`.
- **`audit_service.py`** — `log_audit_action(...)`, single insert helper.

## Chatbot / AI gateway

`app/blueprints/chat/` implements a stateful chat widget: `ChatSession` +
`ChatMessage` persist the conversation; `ai_gateway.py` wraps the Gemini
API (via `AI_API_KEY`, falls back to a static "please call us" reply if
unconfigured); `service.py` handles an in-chat flow that can hand off to
`create_ticket()` when a visitor wants to open a support ticket without
leaving the chat.

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL, GRAPH_*, etc. (no .env.example currently checked in — copy from another dev)
flask db upgrade        # run migrations
python run.py            # http://localhost:8000
```

`run.py` calls `load_dotenv()` before importing the app, so `.env` in the
repo root is picked up automatically. `project_settings.py` selects
`DevelopmentConfig` unless `APP_ENV=production`.

## Migrations

Alembic, via Flask-Migrate: `flask db migrate -m "..."`, `flask db upgrade`.
The DB engine (originally SQL Server, now MariaDB) auto-generates FK
constraint names when none is given explicitly — migrations that drop such
FKs discover the name dynamically via `sa.inspect(bind).get_foreign_keys(table)`
rather than hardcoding it (see `migrations/versions/f298e52b1957_remove_local_users_and_roles.py`
for the pattern). Also note: dropping a whole table drops its indexes too —
an explicit `drop_index` immediately before `drop_table` is redundant on any
engine, and MariaDB (unlike SQL Server) actively rejects it if that index
still backs a live FK constraint (see `2951b6b5d103_replace_password_auth_with_microsoft_.py`,
fixed when migrating off SQL Server).

## Deployment notes

- Dev server (`waitress`) is not for production — use `gunicorn` behind a
  reverse proxy (nginx/IIS) that terminates TLS.
- `production.config.env` + `apply-config.sh` (repo root, one level above
  `IA/`) generate `.env` for this app and the sibling frontend from a
  single source of truth, including `GRAPH_*` (the same Azure AD "Tech
  Support" app credentials used for both email and Microsoft login —
  `MS_AUTH_*` isn't set separately since it defaults to these). B1's
  database is MariaDB (`DB1_*` vars). The ticket platform (B2/F2) has its
  own separate config, not managed from this file.
- Security headers (CSP, HSTS in prod, X-Frame-Options, etc.) are set
  unconditionally in `app/__init__.py`'s `after_request` hook — no reverse
  proxy configuration needed for those.
