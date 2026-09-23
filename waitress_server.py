"""
Windows WSGI entry point — used by IIS HttpPlatformHandler.
IIS passes the port via %HTTP_PLATFORM_PORT% env var.
"""
import os
from dotenv import load_dotenv

load_dotenv()

from waitress import serve  # noqa: E402
from app import create_app  # noqa: E402

application = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('HTTP_PLATFORM_PORT', 8000))
    host = '127.0.0.1'
    threads = int(os.environ.get('WAITRESS_THREADS', 8))

    print(f'[waitress] Starting on {host}:{port} with {threads} threads', flush=True)
    serve(
        application,
        host=host,
        port=port,
        threads=threads,
        # Cap open connections so a traffic spike queues at IIS instead of
        # exhausting this process's memory; slow/idle clients are dropped.
        connection_limit=int(os.environ.get('WAITRESS_CONNECTION_LIMIT', 200)),
        channel_timeout=120,
        cleanup_interval=30,
        # Largest route (company MSI upload) is 250 MB; Flask enforces the
        # per-route limits, this is only the outer bound.
        max_request_body_size=260 * 1024 * 1024,
        ident=None,   # don't advertise "waitress" in the Server header
    )
