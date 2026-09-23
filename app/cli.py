"""Commands for Plesk "Scheduled Tasks" or a shell on the server, e.g.

    flask --app wsgi chatbot reindex
    flask --app wsgi maintenance purge

Both also run automatically (knowledge re-index when older than
CHAT_KNOWLEDGE_MAX_AGE_HOURS, clean-up once a day), so scheduling them is
optional.
"""
from __future__ import annotations

import json

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    @app.cli.group()
    def chatbot():
        """Chatbot knowledge base."""

    @chatbot.command('reindex')
    def chatbot_reindex():
        """Crawl the website into the chatbot's knowledge now."""
        from app.services.site_crawler import rebuild_knowledge
        click.echo(json.dumps(rebuild_knowledge(), indent=2, default=str))

    @app.cli.group()
    def maintenance():
        """Data retention."""

    @maintenance.command('purge')
    def maintenance_purge():
        """Delete data older than the RETENTION_* settings."""
        from app.services.maintenance import purge_old_data
        click.echo(json.dumps(purge_old_data(), indent=2))
