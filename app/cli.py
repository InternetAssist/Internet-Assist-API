"""Commands for Plesk "Scheduled Tasks" or a shell on the server, e.g.

    flask --app wsgi maintenance purge

Nothing runs automatically -- the API does no background work. Run it by
hand, or schedule it in Plesk -> Scheduled Tasks if you want it regular.
"""
from __future__ import annotations

import json

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    @app.cli.group()
    def maintenance():
        """Data retention."""

    @maintenance.command('purge')
    def maintenance_purge():
        """Delete data older than the RETENTION_* settings."""
        from app.services.maintenance import purge_old_data
        click.echo(json.dumps(purge_old_data(), indent=2))
