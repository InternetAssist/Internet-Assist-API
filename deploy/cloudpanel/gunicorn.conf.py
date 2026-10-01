"""gunicorn settings for the API on the CloudPanel server.

    gunicorn -c deploy/cloudpanel/gunicorn.conf.py wsgi:app

CloudPanel's nginx terminates HTTPS for api.ia.uk and forwards to this port
on localhost. Every value can be overridden with an environment variable in
the systemd unit (ia-api.service).
"""
import os

# Only reachable from CloudPanel's nginx on this machine -- never directly
# from the internet.
bind = os.getenv("GUNICORN_BIND", "127.0.0.1:8090")

# One worker process with a pool of threads. The API is I/O-bound (database
# on another server, Microsoft Graph email), so threads handle concurrency
# well, and a single process keeps the in-memory rate limits exact (each
# extra worker would get its own separate counts). Raise GUNICORN_WORKERS
# only if CPU becomes the bottleneck.
workers = int(os.getenv("GUNICORN_WORKERS", "1"))
worker_class = "gthread"
threads = int(os.getenv("GUNICORN_THREADS", "8"))

# Large enough for the 250 MB admin MSI upload/download on a slow line;
# everything else answers in milliseconds.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5

# Replace the worker after this many requests (staggered by the jitter) so
# any slow memory growth in a long-running process is cleared regularly.
# The master starts the fresh worker before the old one exits.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "2000"))
max_requests_jitter = 200

# Trust X-Forwarded-* headers only from nginx on this machine. (The app
# also applies ProxyFix for one proxy hop in production.)
forwarded_allow_ips = "127.0.0.1"

# Each worker loads the app itself (not the master). This way a reload
# (`systemctl reload ia-api` -> HUP) actually picks up new code, and nothing
# created during start-up (database pool, HTTP clients) is shared across a
# fork. With one worker there's no memory to save by preloading anyway.
preload_app = False

# Logs go to the systemd journal: `journalctl -u ia-api -f`
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")
# Don't log query strings (they can carry tokens on the OAuth callback).
access_log_format = '%(h)s "%(m)s %(U)s %(H)s" %(s)s %(B)s %(M)sms "%(a)s"'

proc_name = "ia-api"
