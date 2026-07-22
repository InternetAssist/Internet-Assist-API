"""Seed a starter set of chat_qa_cache entries.

Run against any environment with the venv active and the right .env loaded:

    python scripts/seed_chat_cache.py

Safe to re-run -- skips any question that's already stored (exact
normalized-text match) instead of creating duplicates.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import create_app
from app.extensions import db
from app.blueprints.chat.service import FORMS
from app.models.chat_qa_cache import ChatQaCache
from app.services import chat_cache_service

SEEDS = [
    dict(
        message='What services do you offer?',
        reply=(
            'We offer **Managed IT Support**, **Cybersecurity**, **Cloud Solutions** '
            '(Microsoft 365 / Azure), **Networking**, **Business Continuity & Disaster '
            'Recovery**, and **VoIP & Communications**. Let me know if you would like '
            'more detail on any of these.'
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='My wifi is off, what should I do?',
        reply=(
            'Try restarting your router first — unplug it for about 30 seconds, then '
            'plug it back in and wait a couple of minutes. If it is still not '
            'connecting after that, check that all cables are firmly seated, and give '
            'us a call if the issue persists.'
        ),
        action=None, action_payload=None,
    ),
    dict(
        message="What's your phone number and where are you based?",
        reply=(
            'We are based in **Maldon, Essex**, and you can reach us on '
            '**01621 840014**. We support businesses across Essex including '
            'Chelmsford, Colchester, Braintree and the surrounding towns.'
        ),
        action=None, action_payload=None,
    ),
    dict(
        message="I'd like to get in touch about a general enquiry",
        reply="I'll get the contact form ready for you.",
        action='show_form', action_payload=FORMS['contact'],
    ),
    dict(
        message='I need urgent remote support right now, my computer is broken',
        reply='Let me connect you with our support team right away.',
        action='show_form', action_payload=FORMS['remote_support'],
    ),
    dict(
        message='How much would it cost to get a quote for cyber security services?',
        reply="I'll take you to our quote request page where you can tell us about your needs.",
        action='redirect', action_payload={'url': '/quote', 'label': 'Get a Quote'},
    ),
    dict(
        message='Do you have any job openings at the moment?',
        reply='Check out our current openings on the careers page.',
        action='redirect', action_payload={'url': '/careers', 'label': 'View Open Positions'},
    ),
]


def main() -> None:
    app = create_app()
    with app.app_context():
        created, skipped = 0, 0
        for seed in SEEDS:
            normalized = chat_cache_service._normalize(seed['message'])
            if ChatQaCache.query.filter_by(question_normalized=normalized).first():
                skipped += 1
                continue
            chat_cache_service.store_reply(
                message=seed['message'],
                reply=seed['reply'],
                action=seed['action'],
                action_payload=seed['action_payload'],
                model_name='seed',
            )
            created += 1
        db.session.commit()
        print(f'chat_qa_cache seed: {created} created, {skipped} already present')


if __name__ == '__main__':
    main()
