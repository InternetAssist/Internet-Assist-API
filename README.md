# Internet Assist API

Flask 3 REST API for Internet Assist (ia.uk) — an IT support company based in Maldon, Essex.

---

## Quick start (local / Mac / Linux)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # fill in DATABASE_URL at minimum
flask --app run:app run --debug
```

---

## Deploy to Azure

```bash
# Edit .env: comment LOCAL lines, uncomment AZURE lines
bash deploy.sh
```

---

## Install guide — Windows Server

### Prerequisites

| Software | Version | Notes |
|----------|---------|-------|
| Python | 3.12+ | [python.org](https://python.org) — tick "Add to PATH" |
| SQL Server Express | 2019+ | Free — [microsoft.com](https://www.microsoft.com/en-gb/sql-server/sql-server-downloads) |
| SQL Server Management Studio | Any | Optional but useful |
| Git | Latest | [git-scm.com](https://git-scm.com) |

### 1. Clone and install

Open **Command Prompt as Administrator**:

```bat
git clone <repo-url> C:\inetpub\ia-api
cd C:\inetpub\ia-api

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install waitress          :: Windows WSGI server (replaces gunicorn)
```

### 2. Create the SQL Server database

In SQL Server Management Studio (or sqlcmd):

```sql
CREATE DATABASE InternetAssist;
GO
CREATE LOGIN iaadmin WITH PASSWORD = 'YourStrongPass123!';
GO
USE InternetAssist;
CREATE USER iaadmin FOR LOGIN iaadmin;
ALTER ROLE db_owner ADD MEMBER iaadmin;
GO
```

### 3. Configure environment

Copy `.env.example` to `.env` and fill in:

```ini
APP_ENV=production
DATABASE_URL=mssql+pymssql://iaadmin:YourStrongPass123!@localhost:1433/InternetAssist
SECRET_KEY=<random 64-char string>
JWT_SECRET_KEY=<random 64-char string>
CORS_ORIGINS=https://your-frontend-domain.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=<your Gmail>
SMTP_PASSWORD=<app password>
NOTIFY_EMAIL_1=<admin email>
PUBLIC_CONTACT_EMAIL=enquiries@internetassist.co.uk
PUBLIC_CONTACT_PHONE=01621 840014
TICKET_API_URL=https://ticketflow-api.azurewebsites.net
UPLOAD_FOLDER=C:\inetpub\ia-api\uploads
```

> **Windows path note:** Set `UPLOAD_FOLDER` to a Windows path, e.g. `C:\inetpub\ia-api\uploads`. Create the folder manually or it will be created at runtime.

### 4. Run database migrations and seed

```bat
.venv\Scripts\activate
set FLASK_APP=wsgi:app
flask db upgrade
flask seed
```

### 5. Run with Waitress (development / quick test)

```bat
.venv\Scripts\activate
waitress-serve --host=0.0.0.0 --port=8000 wsgi:app
```

### 6. Run as a Windows Service (production)

Install **NSSM** (Non-Sucking Service Manager): [nssm.cc](https://nssm.cc)

```bat
nssm install InternetAssistAPI "C:\inetpub\ia-api\.venv\Scripts\waitress-serve.exe"
nssm set InternetAssistAPI AppParameters --host=0.0.0.0 --port=8000 wsgi:app
nssm set InternetAssistAPI AppDirectory C:\inetpub\ia-api
nssm set InternetAssistAPI AppEnvironmentExtra APP_ENV=production
nssm start InternetAssistAPI
```

The service will auto-start on boot and restart on failure.

### 7. IIS reverse proxy (optional — for SSL / custom domain)

Install the **URL Rewrite** and **Application Request Routing** IIS modules, then add a site that proxies `http://localhost:8000`.

---

## Environment differences

| Setting | Development | Testing | Production |
|---------|------------|---------|------------|
| `APP_ENV` | `development` | `testing` | `production` |
| `DEBUG` | `True` | `False` | `False` |
| `DATABASE_URL` | Local SQL Server or SQLite | `sqlite:///:memory:` | Azure SQL / prod SQL Server |
| `SECRET_KEY` | `dev-secret` (default) | `dev-secret` (default) | **Must set strong random value** |
| `JWT_SECRET_KEY` | `dev-secret` (default) | `dev-secret` (default) | **Must set strong random value** |
| `CORS_ORIGINS` | `http://localhost:5173` | Any | Production domains only |
| `SESSION_COOKIE_SECURE` | Off | Off | **On** (HTTPS required) |
| `RATELIMIT_STORAGE_URI` | `memory://` (per-process) | `memory://` | `memory://` or `REDIS_URL` for multi-worker |
| `ProxyFix` middleware | Off | Off | **On** (reads real IP from X-Forwarded-For) |
| `UPLOAD_FOLDER` | `/tmp/internet-assist-uploads` | `/tmp/...` | `/tmp/...` or Windows path |

### What to change per environment

**Development** (`APP_ENV=development`):
- Set `DATABASE_URL` to your local SQL Server or use SQLite (`sqlite:///dev.db`)
- All other settings optional — app starts without email/ticket config

**Testing** (`APP_ENV=testing`):
- Uses `sqlite:///:memory:` automatically — no database needed
- Rate limiter uses in-memory store, CSRF disabled
- Set `JWT_ACCESS_TOKEN_EXPIRES` to 5 minutes (already set in `TestingConfig`)

**Production** (`APP_ENV=production`):
- **Mandatory:** `SECRET_KEY`, `JWT_SECRET_KEY`, `DATABASE_URL`
- **Recommended:** `SMTP_*`, `NOTIFY_EMAIL_1`, `TICKET_API_URL`, `CORS_ORIGINS`
- On Azure: all set via App Service application settings (deploy.sh pushes them)
- On Windows Server: set in `.env` file in the app directory
- If running multiple gunicorn/waitress workers, set `REDIS_URL` so rate limiting is shared across workers

---

## Key endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/contact` | Contact form submission |
| POST | `/quotes` | Quote request |
| POST | `/job-applications` | Job application (multipart/form-data) |
| POST | `/remote-support-request` | Remote support request |
| GET | `/job-postings` | List open positions |
| GET | `/healthz` | Liveness probe |
| GET | `/readyz` | Readiness probe (hits database) |
| GET | `/docs` | Swagger UI |
| POST | `/admin/login` | Get JWT token |
| GET | `/admin/contacts` | Admin — contacts (JWT required) |
| GET | `/admin/quotes` | Admin — quotes (JWT required) |
| GET | `/admin/jobs` | Admin — job applications (JWT required) |
| GET | `/admin/stats` | Admin — dashboard stats (JWT required) |
| GET | `/admin/audit-logs` | Admin — audit trail (JWT required) |

---

## Code structure

```
IA/
├── project_settings.py        # All config classes (Dev/Test/Prod) + env vars
├── wsgi.py                    # Production entry point — gunicorn/waitress loads this
├── run.py                     # Dev entry point — python run.py for local
├── startup.sh                 # Azure App Service startup: migrate → seed → gunicorn
├── deploy.sh                  # Azure CLI deploy script — run once to ship
├── .env                       # Secrets & env vars (never committed)
├── requirements.txt           # Pinned deps for Azure Oryx build
│
└── app/
    ├── __init__.py            # create_app() factory — wires everything together
    ├── config.py              # Thin shim: re-exports project_settings
    ├── extensions.py          # Flask extension instances (db, jwt, cors, limiter…)
    ├── errors.py              # Global error handlers (404, 422, 500 → JSON)
    ├── logging.py             # Structured JSON logging (structlog)
    │
    ├── blueprints/            # Route handlers, one folder per domain
    │   ├── health/            # GET /healthz — liveness probe
    │   ├── public/
    │   │   ├── contact_routes.py        # POST /contact
    │   │   ├── quote_routes.py          # POST /quotes
    │   │   ├── job_routes.py            # POST /job-applications
    │   │   ├── job_posting_routes.py    # GET  /job-postings
    │   │   └── remote_support_routes.py # POST /remote-support-request
    │   └── admin/
    │       └── routes.py      # JWT-protected admin CRUD (contacts, quotes, jobs, users)
    │
    ├── models/                # SQLAlchemy ORM models (one file per table)
    │   ├── base.py            # Shared UUID primary key mixin
    │   ├── user.py / role.py  # Auth — admin users & roles
    │   ├── contact.py         # Contact form submissions
    │   ├── quote.py           # Quote requests
    │   ├── job_application.py # CV / job applications
    │   ├── job_posting.py     # Open positions listed on the site
    │   └── audit_log.py       # Immutable audit trail for all write actions
    │
    ├── schemas/               # Marshmallow request/response schemas (validation + docs)
    │   ├── base.py            # Shared envelope: {data, error, meta}
    │   ├── public.py          # Contact, Quote, Job, RemoteSupport input schemas
    │   └── admin.py           # Admin-only schemas
    │
    ├── services/              # Business logic that crosses multiple models
    │   ├── email_service.py   # SMTP email sending (notifications on new submissions)
    │   ├── ticket_service.py  # Creates support tickets via external TicketFlow API
    │   ├── audit_service.py   # Writes to audit_log table
    │   └── seeder.py          # Seeds initial admin user on first deploy
    │
    └── utils/
        ├── response.py        # envelope() / error_envelope() — consistent JSON responses
        └── decorators.py      # @roles_required JWT decorator
```

---

