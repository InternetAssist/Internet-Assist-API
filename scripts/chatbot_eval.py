"""Accuracy check for the chatbot's website answers -- run after changing the
search, relevance rules or site content:

    python scripts/chatbot_eval.py

Each question lists the page(s) that correctly answer it. For every question
it reports how the chatbot would treat it WITHOUT the AI (what visitors get
when Gemini is unavailable): which page it would quote, or that it would
decline. Declining is never counted as wrong -- quoting the wrong page is.
Needs the knowledge base indexed first (`flask chatbot reindex`).

    python scripts/chatbot_eval.py --calibrate

With AI_API_KEY set and the site indexed with meaning vectors, measures how
similar off-topic questions and wrong pages look to the real embedding
model, and stores thresholds just above them (so neither gets through). Until
this is run, meaning only helps rank results. Re-run after changing
EMBEDDING_MODEL or adding many questions here. `--reset-calibration` removes
the thresholds.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from app import create_app  # noqa: E402
from app.blueprints.chat.service import _website_answer  # noqa: E402
from app.services import chat_guard, knowledge_service  # noqa: E402

OFF = 'off_topic'
# The chatbot should offer the support form + phone number, not quote a page.
SUPPORT = 'support'

# (question, acceptable source URLs -- or OFF when it must be refused)
CASES: list[tuple[str, set[str] | str]] = [
    # Where / when / who
    ('Where is your office located', {'/contact'}),
    ('Where are you based?', {'/contact'}),
    ("What's your address?", {'/contact'}),
    ('What are your opening hours?', {'/contact'}),
    ('What is your phone number?', {'/contact'}),
    ('How do I contact you?', {'/contact'}),
    ('Who owns the company?', {'/contact'}),
    ('When was Internet Assist founded?', {'/contact', '/about', '/'}),
    # Services
    ('What services do you offer?', {'/services', '/'}),
    ('Do you do Office 365 backups?', {'/backup-recovery'}),
    ('Can you migrate us to Microsoft 365?', {'/cloud-services'}),
    ('Can you help us get Cyber Essentials?', {'/cyber-security'}),
    ('Do you offer VoIP phone systems?', {'/communications'}),
    ('Do you build websites?', {'/web-design'}),
    ('Can you build a mobile app?', {'/web-design', '/software-development'}),
    ('Do you provide managed IT support?', {'/it-support'}),
    ('Do you offer firewall management?', {'/cyber-security'}),
    ('Do you do disaster recovery?', {'/backup-recovery'}),
    ('Do you install network cabling and servers?', {'/infrastructure'}),
    # Areas
    ('Do you cover Chelmsford?', {'/it-support-chelmsford'}),
    ('Do you provide IT support in Colchester?', {'/it-support-colchester'}),
    ('Can an engineer come on site in Witham?', {'/it-support-witham'}),
    # Careers
    ('Are you hiring?', {'/careers'}),
    # Written after tuning, to check the rules generalise
    ('What is your postcode?', {'/contact'}),
    ('Are you open on Saturdays?', {'/contact'}),
    ('Can I email you?', {'/contact'}),
    ('Do you have any job vacancies?', {'/careers'}),
    ('How can I apply for a job?', {'/careers'}),
    ('Do you sell Office 365 licences?', {'/cloud-services'}),
    ('Can you protect us from ransomware?', {'/cyber-security', '/backup-recovery'}),
    ('Do you offer 24/7 support?', {'/it-support'}),
    ('Can you replace our old phone system?', {'/communications'}),
    ('Do you do on-site IT support in Braintree?', {'/it-support-braintree'}),
    ('Do you cover London?', {'/it-support-london'}),
    ('Do you use Hornetsecurity?', {'/backup-recovery'}),
    ('Can you build us bespoke software?', {'/software-development'}),
    ('Do you do WiFi installation?', {'/infrastructure'}),
    ('How quickly do you respond to issues?', {'/it-support'}),
    ('Do you work with charities?', {'/it-support'}),
    ('Is your office near Chelmsford?', {'/contact', '/it-support-chelmsford'}),
    ('What is the weather in Maldon?', OFF),
    ('Recommend a good film', OFF),
    # Borderline: refusing or quoting the phone-systems page are both fine.
    ('What is the best phone to buy?', {OFF, '/communications'}),
    ('Are you in Maldon?', {'/contact'}),
    # Asking for help -> support offer
    ('I need urgent IT support', SUPPORT),
    ('Our server is down', SUPPORT),
    ('Emergency - we have been hacked', SUPPORT),
    ('I need help with my computer', SUPPORT),
    ('My outlook keeps crashing', SUPPORT),
    ("I can't log in to my email", SUPPORT),
    # General IT support questions must not quote a random town page
    ('Do you offer IT support?', {'/it-support'}),
    ('How much does IT support cost?', {'/it-support', '/quote'}),
    # Off-topic
    ('write me a poem about cats', OFF),
    ("what's the capital of France?", OFF),
    ('who won the football last night', OFF),
    ('best pasta recipe', OFF),
    ('tell me a joke', OFF),
    ('how tall is the eiffel tower', OFF),
]


_MARGIN = 0.02


def calibrate() -> int:
    from datetime import datetime, timezone

    from app.models.site_setting import SiteSetting
    from app.services import embedding_service

    if not knowledge_service.meaning_active():
        print('Meaning search is not active: set AI_API_KEY, then run `flask chatbot reindex` so the site '
              'gets meaning vectors.')
        return 1

    off_topic, wrong_top, right_best = [], [], []
    for question, expected in CASES:
        # The same filtered sections the chatbot answers from.
        hits = [h for h in knowledge_service.relevant_sections(question, 30) if h.similarity is not None]
        if not hits:
            continue
        top = max(hits, key=lambda h: h.similarity)
        if expected == OFF:
            off_topic.append((top.similarity, question))
        elif isinstance(expected, set) and OFF not in expected:
            right = [h.similarity for h in hits if h.url in expected]
            if right:
                right_best.append((max(right), question))
            # Routed questions ("opening hours", "are you hiring?") are sent
            # to their page before meaning is consulted, so meaning picking a
            # different page for them never reaches a visitor.
            if top.url not in expected and knowledge_service.route(question) is None:
                wrong_top.append((top.similarity, question, top.url))

    if not off_topic or not right_best:
        print('The embedding API returned nothing for these questions (outage or rate limit) -- '
              'nothing saved. Try again in a minute.')
        return 1
    relevant = round(max(sim for sim, _ in off_topic) + _MARGIN, 4)
    answer = round(max([relevant] + [sim + _MARGIN for sim, _, _ in wrong_top]), 4)
    if answer >= 0.99:
        # Some wrong page is as close as it gets -- meaning alone can't be
        # trusted to pick the answer with this model and content.
        answer = None
    print('Most similar off-topic questions:')
    for sim, q in sorted(off_topic, reverse=True)[:3]:
        print(f'  {sim:.3f}  {q!r}')
    print('Most similar WRONG top pages:')
    for sim, q, url in sorted(wrong_top, reverse=True)[:3]:
        print(f'  {sim:.3f}  {q!r} -> {url}')
    print(f'\nrelevant >= {relevant}   answer >= {answer if answer is not None else "(disabled)"}')
    if answer is not None:
        answerable = sum(1 for sim, _ in right_best if sim >= answer)
        print(f'{answerable}/{len(right_best)} on-topic questions reach the answer threshold by meaning alone')
    else:
        print('No safe answer threshold -- answers still require the keyword checks.')

    SiteSetting.upsert(knowledge_service.THRESHOLDS_KEY, {
        'model': embedding_service.model_name(),
        'relevant': relevant,
        'answer': answer,
        'calibrated_at': datetime.now(timezone.utc).isoformat(),
        'questions': len(CASES),
    })
    print('Saved. Running the evaluation with them:\n')
    return 0


def main() -> int:
    app = create_app()
    if '--calibrate' in sys.argv or '--reset-calibration' in sys.argv:
        with app.app_context():
            if '--reset-calibration' in sys.argv:
                from app.models.site_setting import SiteSetting
                SiteSetting.upsert(knowledge_service.THRESHOLDS_KEY, None)
                print('Calibration removed -- meaning is used for ranking only.')
                return 0
            if calibrate():
                return 1
    right = wrong = declined = 0
    with app.app_context():
        for question, expected in CASES:
            kind = chat_guard.classify(question, knowledge_service.search(question, 1), False)
            if expected == SUPPORT:
                ok = chat_guard.support_intent(question) is not None
                outcome = 'support offered' if ok else f'NO support offer ({kind})'
            elif expected == OFF or (OFF in expected and kind != chat_guard.RELEVANT):
                ok = kind != chat_guard.RELEVANT
                outcome = 'refused' if ok else f'NOT refused ({kind})'
            elif chat_guard.support_intent(question) is not None:
                ok = False
                outcome = 'support offered instead of answering'
            elif kind != chat_guard.RELEVANT:
                ok = False
                outcome = f'wrongly refused ({kind})'
            else:
                answer = _website_answer(knowledge_service.relevant_sections(question, 5), question)
                if answer is None:
                    declined += 1
                    print(f'  declined  {question!r}')
                    continue
                quoted = answer[1][0]['url']
                ok = quoted in expected
                outcome = f'quoted {quoted}' + ('' if ok else f'  (expected {sorted(expected)})')
            right += ok
            wrong += not ok
            print(f"  {'ok   ' if ok else 'WRONG'}     {question!r:48} {outcome}")
    total = right + wrong + declined
    with app.app_context():
        mode = 'meaning + keywords' if knowledge_service.meaning_active() else 'keywords only (no meaning vectors)'
        calibrated = 'calibrated' if knowledge_service.semantic_thresholds() else 'not calibrated'
    print(f'\nSearch: {mode}, {calibrated}')
    print(f'{right}/{total} right, {wrong} wrong, {declined} declined (safe, but a missed answer)')
    return 1 if wrong else 0


if __name__ == '__main__':
    sys.exit(main())
