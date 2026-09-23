"""Decides, without calling the AI, whether a chat message is worth an AI call.

Greetings and thanks get a canned reply. Questions that are about neither
Internet Assist nor IT support ("write me a poem", "who won the match") get a
polite redirect instead of spending Gemini credits. Only what's left reaches
the AI, grounded on matching website content.

A message counts as relevant when any of these hold:
  * it's one of the common questions knowledge_service routes (where are you,
    opening hours, jobs...), or uses an IT-support or company term
    (DOMAIN_TERMS, or "IT" in capitals). A town name alone doesn't count --
    "what's the weather in Maldon?" isn't about us.
  * it shares at least two meaningful words with one section of the website,
    or one rare, site-specific word (a town we cover, a product we sell) when
    most of its other words appear on the site too
  * it's a short follow-up in a conversation that was already on-topic
  * its meaning is close to a section of the site (calibrated threshold)
"""
from __future__ import annotations

import re

from app.services.knowledge_service import Hit, route, semantic_thresholds, tokenize, vocabulary_share

GREETING = 'greeting'
THANKS = 'thanks'
UNCLEAR = 'unclear'
OFF_TOPIC = 'off_topic'
RELEVANT = 'relevant'

_GREETING_RE = re.compile(
    r"^\s*(hi+|hello+|hey+|hiya|howdy|yo|good\s+(morning|afternoon|evening|day)|greetings)"
    r"(\s+(there|all|team|ia|internet assist))?\s*[!.,?]*\s*$",
    re.I,
)
_THANKS_RE = re.compile(
    r"^\s*(thanks?( you)?( (so|very) much)?|thx|ty|cheers|great|perfect|cool|ok(ay)?|bye|goodbye|"
    r"that'?s (all|great|helpful)|got it|brilliant|lovely)\s*[!.,]*\s*$",
    re.I,
)
_IT_ACRONYM_RE = re.compile(r'\bIT\b')

# Topics the chatbot is for: the company itself, its services, and IT support
# problems a visitor might want help with. Kept deliberately broad on the IT
# side -- a visitor with an IT problem is exactly who this site is for.
_DOMAIN_WORDS = '''
internet assist ia company owner founder director staff team engineer technician office address location
located based area areas cover coverage near nearby local visit hours open opening contact phone
call email reach support helpdesk help desk ticket
service price pricing cost costs quote quotation contract monthly fee package plan sla response onsite remote
job career careers vacancy vacancies hiring apply application role recruit cv interview salary
accreditation certified certification iso partner cyber essentials review testimonial client customer
computer laptop pc desktop mac macbook server servers network networking wifi wi-fi wireless router switch
firewall vpn broadband internet connection connectivity fibre lan ethernet dns domain hosting host website web
email outlook exchange mailbox spam phishing microsoft office 365 m365 teams sharepoint onedrive azure aws cloud
windows macos linux ios android iphone ipad tablet mobile phone printer printing scanner monitor screen keyboard
mouse hardware software app application install installation update upgrade licence license migration
password login log-in sign-in mfa 2fa authentication account locked reset
security secure virus malware ransomware hack hacked breach antivirus endpoint encryption compliance gdpr
backup backups restore recovery disaster continuity data storage nas sync
voip telephone telephony phones 3cx pbx isdn calling
slow crash crashing frozen freeze error broken not working fix fixing problem issue issues troubleshoot
infrastructure virtualisation virtualization hyper-v vmware rmm monitoring
development developer bespoke portal api integration design seo ecommerce
'''
DOMAIN_TERMS = frozenset(tokenize(_DOMAIN_WORDS))

# Someone asking for help now, not asking about us. Answered straight away
# with the support line and the remote-support form -- no AI, no page quote.
URGENT = 'urgent'
# Describing a technical problem. The AI can suggest first steps when it's
# available; otherwise they get the same support offer.
TROUBLESHOOT = 'troubleshoot'

_URGENT_RE = re.compile(
    r"\burgent(ly)?\b|\bemergency\b|\basap\b|\bright now\b|\bimmediately\b"
    r"|\bneed (some |urgent |immediate )?(it |tech(nical)? )?(help|support|assistance)\b"
    r"|\b(is|are|has|have|keeps?) (gone )?(down|offline)\b|\bstopped working\b"
    r"|\b(been|got|we'?re|were|was|am|are) hacked\b|\bransomware (attack|infection)\b|\bdata breach\b"
    r"|\bnothing (is )?working\b|\bspeak to (an? )?(engineer|technician)\b",
    re.I,
)
_TROUBLESHOOT_RE = re.compile(
    r"\bnot working\b|\bisn'?t working\b|\bdoesn'?t work\b|\bwon'?t (start|boot|connect|load|open|print|turn on)\b"
    r"|\bcan'?t (log ?in|sign ?in|connect|access|print|send|receive|open|get into)\b|\bcannot (log ?in|connect|access)\b"
    r"|\bkeeps? (crashing|freezing|dropping|disconnecting|restarting)\b|\b(crashed|frozen|broken)\b"
    r"|\berror (message|code)\b|\bblue screen\b|\bvirus\b|\bmalware\b|\bpop-?ups?\b|\bvery slow\b|\brunning slow\b",
    re.I,
)


def support_intent(message: str) -> str | None:
    if _URGENT_RE.search(message):
        return URGENT
    if _TROUBLESHOOT_RE.search(message):
        return TROUBLESHOOT
    return None


_FOLLOWUP_MAX_TOKENS = 12
_MIN_VOCABULARY_SHARE = 0.5


def classify(message: str, hits: list[Hit], in_relevant_conversation: bool) -> str:
    if _GREETING_RE.match(message):
        return GREETING
    if _THANKS_RE.match(message):
        return THANKS

    tokens = tokenize(message)
    if not tokens:
        return UNCLEAR

    # Recognised common questions (where/when/who/jobs/services) always are.
    if route(message) or DOMAIN_TERMS.intersection(tokens) or _IT_ACRONYM_RE.search(message):
        return RELEVANT
    if hits and len(hits[0].matched) >= 2:
        return RELEVANT
    # One rare word counts ("Chelmsford", "Hornetsecurity") only if the rest
    # of the question is also site vocabulary -- "who won the football last
    # night" also matches one rare word ("night"), but nothing else in it
    # appears on the site.
    if hits and hits[0].distinctive and vocabulary_share(message) >= _MIN_VOCABULARY_SHARE:
        return RELEVANT
    if in_relevant_conversation and len(tokens) <= _FOLLOWUP_MAX_TOKENS:
        return RELEVANT
    # Meaning: close enough to something on the site even with none of its
    # words ("how fast do your engineers turn up?"). Only once thresholds
    # have been calibrated for the embedding model in use.
    thresholds = semantic_thresholds()
    if thresholds and hits and hits[0].similarity is not None and hits[0].similarity >= thresholds['relevant']:
        return RELEVANT
    return OFF_TOPIC
