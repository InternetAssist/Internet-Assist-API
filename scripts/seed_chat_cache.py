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

    # ── Per-service questions, grounded in the real service page copy ──────────
    dict(
        message='What does your IT support service include?',
        reply=(
            "Our **Managed IT Support** is your outsourced IT department — proactive "
            "monitoring, a helpdesk, and on-site engineers based in Maldon. Average "
            "response time is **under 15 minutes**, with a **92% first-call fix rate**. "
            "Cover ranges from standard weekday hours up to full 24/7/365 monitoring."
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='Can you help us move to Microsoft 365 or the cloud?',
        reply=(
            "Yes — we're a certified **Microsoft Partner** handling **Microsoft 365, "
            "Azure, AWS and Hosted Desktop** migrations, run out of our UK Tier 2 data "
            "centre with **99.99% uptime**. We start with a free cloud readiness audit, "
            "then a phased, out-of-hours migration so there's no disruption."
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='What cyber security services do you provide?',
        reply=(
            "Cyber Essentials Plus certification, firewall management, endpoint "
            "protection, email security and security reviews, backed by a **24/7 SOC**. "
            "We start with a free security review scoring your current setup against "
            "Cyber Essentials and flagging the highest-risk gaps first."
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='Tell me about Cyber Essentials certification',
        reply=(
            "**Cyber Essentials** (and Cyber Essentials Plus) is a UK government-backed "
            "certification proving your business meets a baseline of cyber security "
            "controls — increasingly required by clients, insurers and public sector "
            "tenders. We're certified ourselves and can guide you through certification "
            "end to end, starting with a free security review."
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='What backup and disaster recovery options do you have?',
        reply=(
            "A fully managed backup service powered by **Hornetsecurity**, covering "
            "Microsoft 365, physical servers and endpoints with encrypted, automated "
            "backups. We run a **99.98% backup success rate** with typical recovery "
            "times **under 4 hours**, and support granular restores down to a single "
            "file or email."
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='Do you do business phone systems or VoIP?',
        reply=(
            "Yes — cloud VoIP, **3CX** and **Microsoft Teams calling**, replacing "
            "ISDN/analogue lines and on-prem PBXs before the 2027 ISDN switch-off. "
            "Most businesses cut their phone bill by 40-60% moving over. We port your "
            "numbers, configure everything and install on-site."
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='Can you set up our office network and servers?',
        reply=(
            "Yes — networks, servers, virtualisation, storage and structured cabling, "
            "designed, installed and maintained by our own engineers, not subcontracted "
            "out. Every project starts with a free site survey, and you get full network "
            "diagrams and documentation afterwards."
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='Do you build custom software or apps?',
        reply=(
            "Yes — bespoke software, web apps, mobile apps and API integrations, built "
            "around your actual workflow rather than a template. You own 100% of the "
            "code from day one, and as an MSP too, we make sure whatever we build "
            "integrates cleanly with your Microsoft 365, security and backup setup."
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='Do you do web design?',
        reply=(
            "Yes — bespoke web design, web apps and native iOS/Android apps, plus "
            "ultra-reliable UK hosting and domain management, all from one Essex team "
            "rather than handed off between agencies and hosts."
        ),
        action=None, action_payload=None,
    ),

    # ── General company / logistics questions ───────────────────────────────────
    dict(
        message='What are your business hours and response times?',
        reply=(
            "Standard support cover is **Mon-Fri, 9:00am-5:30pm**, with options up to "
            "**24/7/365** proactive monitoring for businesses that need it. On-site "
            "response is typically **under an hour** for urgent issues across Essex."
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='How long have you been in business and how many clients do you have?',
        reply=(
            "We've been supporting Essex businesses since **1996** — 30+ years — and "
            "currently support **500+ UK clients** with a **99.9% uptime SLA** across "
            "our managed services."
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='Do you cover businesses outside Maldon, like Chelmsford or Colchester?',
        reply=(
            "Yes — we're based in Maldon but cover businesses across Essex, including "
            "Chelmsford, Colchester, Braintree, Witham, Billericay, Basildon and the "
            "surrounding towns, with same-day on-site engineers when needed."
        ),
        action=None, action_payload=None,
    ),
    dict(
        message='Can you take over from our current IT company?',
        reply=(
            "Yes — we handle onboarding and handover from your existing provider, "
            "including documentation and access, with minimal disruption to your team."
        ),
        action=None, action_payload=None,
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
