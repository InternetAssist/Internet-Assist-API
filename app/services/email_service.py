from __future__ import annotations

import base64
import mimetypes
from functools import lru_cache
from html import escape
from pathlib import Path

import requests
from flask import current_app

from app.logging import logger
from app.services import file_settings

# Brand gradient (matches the site's emerald->teal CTA buttons) and logo,
# reused as a shared header across every transactional email so they all
# look like they came from the same place.
_BRAND_GRADIENT = 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)'
_LOGO_PATH = Path(__file__).parent / 'email_assets' / 'ia-logo.png'


@lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    return f"data:image/png;base64,{base64.b64encode(_LOGO_PATH.read_bytes()).decode()}"


def _email_header(title: str) -> str:
    return f"""
        <tr><td style="background:#ffffff;padding:28px 32px 18px;text-align:center">
          <img src="{_logo_data_uri()}" alt="Internet Assist" width="190" style="display:inline-block;height:auto;max-width:190px">
        </td></tr>
        <tr><td style="background:{_BRAND_GRADIENT};padding:18px 32px">
          <h1 style="margin:0;color:#fff;font-size:21px">{escape(title)}</h1>
        </td></tr>"""

_TICKET_COLOURS = {
    'contact':         '#2563eb',
    'quote':           '#7c3aed',
    'job_application': '#059669',
    'remote_support':  '#dc2626',
}

_TICKET_LABELS = {
    'contact':         'Contact Request',
    'quote':           'Quote Request',
    'job_application': 'Job Application',
    'remote_support':  'Remote Support Request',
}

_GRAPH_TOKEN_URL = 'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'
_GRAPH_SEND_URL  = 'https://graph.microsoft.com/v1.0/users/{sender}/sendMail'


def _get_access_token() -> str | None:
    tenant_id     = current_app.config.get('GRAPH_TENANT_ID', '')
    client_id     = current_app.config.get('GRAPH_CLIENT_ID', '')
    client_secret = current_app.config.get('GRAPH_CLIENT_SECRET', '')
    if not (tenant_id and client_id and client_secret):
        return None
    resp = requests.post(
        _GRAPH_TOKEN_URL.format(tenant_id=tenant_id),
        data={
            'grant_type':    'client_credentials',
            'client_id':     client_id,
            'client_secret': client_secret,
            'scope':         'https://graph.microsoft.com/.default',
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()['access_token']


def _graph_send(
    *,
    to: list[str],
    subject: str,
    html: str,
    plain: str,
    reply_to: str | None = None,
    attachments: list[tuple[bytes, str]] | None = None,
) -> None:
    sender = current_app.config.get('GRAPH_SENDER', '')
    token  = _get_access_token()

    message: dict = {
        'subject': subject,
        'body': {'contentType': 'HTML', 'content': html},
        'toRecipients': [{'emailAddress': {'address': addr}} for addr in to],
    }
    if reply_to:
        message['replyTo'] = [{'emailAddress': {'address': reply_to}}]

    if attachments:
        message['attachments'] = []
        for data, filename in attachments:
            ctype, _ = mimetypes.guess_type(filename)
            message['attachments'].append({
                '@odata.type':  '#microsoft.graph.fileAttachment',
                'name':         filename,
                'contentType':  ctype or 'application/octet-stream',
                'contentBytes': base64.b64encode(data).decode(),
            })

    resp = requests.post(
        _GRAPH_SEND_URL.format(sender=sender),
        json={'message': message, 'saveToSentItems': False},
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        timeout=30,
    )
    resp.raise_for_status()


def _graph_configured() -> bool:
    return bool(
        current_app.config.get('GRAPH_TENANT_ID')
        and current_app.config.get('GRAPH_CLIENT_ID')
        and current_app.config.get('GRAPH_CLIENT_SECRET')
        and current_app.config.get('GRAPH_SENDER')
    )


def _build_html(ticket_type: str, ticket_id: str, fields: dict) -> str:
    colour   = _TICKET_COLOURS.get(ticket_type, '#2563eb')
    label    = _TICKET_LABELS.get(ticket_type, 'New Request')
    short_id = ticket_id[:8].upper()

    empty_cell = "<span style='color:#9ca3af'>—</span>"
    rows = ''.join(
        f'<tr>'
        f'<td style="padding:8px 12px;font-weight:600;color:#374151;white-space:nowrap;background:#f9fafb;border-bottom:1px solid #e5e7eb;width:30%">{escape(str(k))}</td>'
        f'<td style="padding:8px 12px;color:#111827;border-bottom:1px solid #e5e7eb">'
        f'{escape(str(v)) if v else empty_cell}</td>'
        f'</tr>'
        for k, v in fields.items()
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">
        {_email_header(label)}
        <tr>
          <td style="padding:16px 32px;background:#f9fafb;border-bottom:2px solid {colour}">
            <span style="font-size:12px;color:#6b7280">Ticket&nbsp;</span>
            <span style="font-size:15px;font-weight:700;color:{colour};letter-spacing:.5px">#{escape(short_id)}</span>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 32px">
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border:1px solid #e5e7eb;border-radius:6px;overflow:hidden">
              {rows}
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb">
            <p style="margin:0;font-size:12px;color:#9ca3af">
              This ticket was automatically generated by the Internet Assist website.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _build_plain(ticket_type: str, ticket_id: str, fields: dict) -> str:
    label = _TICKET_LABELS.get(ticket_type, 'New Request')
    lines = [f"INTERNET ASSIST — {label.upper()}", f"Ticket ID: {ticket_id}", ""]
    for k, v in fields.items():
        lines.append(f"{k}: {v or '—'}")
    lines += ["", "Log in to the admin panel to manage this submission."]
    return "\n".join(lines)


def _recipients() -> list[str]:
    emails = [
        current_app.config.get('NOTIFY_EMAIL_1', ''),
        current_app.config.get('NOTIFY_EMAIL_2', ''),
    ]
    return [e.strip() for e in emails if e.strip()]


_CONFIRMATION_CONFIG: dict[str, dict] = {
    'job_application': {
        'title':   'Application received',
        'intro':   'Thank you for applying to Internet Assist! We\'ve received your CV and will review your application carefully.',
        'eta':     'Within 5 working days',
        'steps': [
            'Our HR team reviews your CV and cover letter',
            'If your profile matches, we\'ll call you to arrange a first chat',
            'We\'ll keep you updated by email at every stage',
        ],
        'subject': 'We\'ve received your application — Internet Assist',
    },
    'contact': {
        'title':   'Message received',
        'intro':   'Thank you for getting in touch with Internet Assist!',
        'eta':     'Within 1 working day',
        'steps': [
            'A member of our team reads your message',
            'We\'ll reply by email or phone, whichever suits your enquiry',
            'If it\'s urgent, call us any time on 01621 840014',
        ],
        'subject': 'We\'ve received your message — Internet Assist',
    },
    'quote': {
        'title':   'Quote request received',
        'intro':   'Thank you for requesting a quote from Internet Assist!',
        'eta':     'Within 2 working days',
        'steps': [
            'Our team reviews your requirements',
            'We prepare a tailored proposal for your business',
            'We\'ll walk you through it and answer any questions',
        ],
        'subject': 'We\'ve received your quote request — Internet Assist',
    },
    'remote_support': {
        'title':   'Support request received',
        'intro':   'Thank you for reaching out to Internet Assist for remote support!',
        'eta':     'Within 30 minutes (business hours)',
        'steps': [
            'A technician is notified of your request immediately',
            'We call or email you to confirm the issue',
            'We connect remotely and get you back up and running',
        ],
        'subject': 'We\'ve received your support request — Internet Assist',
    },
}


def send_confirmation(
    ticket_type: str,
    recipient_email: str,
    recipient_name: str,
    ticket_ref: str | None = None,
    details: dict | None = None,
) -> bool:
    if not _graph_configured():
        logger.info('email_skipped_no_credentials', action='confirmation', ticket_type=ticket_type)
        return True

    cfg = _CONFIRMATION_CONFIG.get(ticket_type)
    if not cfg:
        return False

    first = escape(recipient_name.split()[0] if recipient_name else 'there')

    detail_rows = ''.join(
        f'<tr>'
        f'<td style="padding:10px 16px;font-size:13px;font-weight:600;color:#6b7280;white-space:nowrap;vertical-align:top;width:32%">{escape(str(k))}</td>'
        f'<td style="padding:10px 16px;font-size:14px;color:#111827">{escape(str(v))}</td>'
        f'</tr>'
        for k, v in (details or {}).items() if v
    )
    if ticket_ref:
        detail_rows = (
            f'<tr><td style="padding:10px 16px;font-size:13px;font-weight:600;color:#6b7280;white-space:nowrap;width:32%">Reference</td>'
            f'<td style="padding:10px 16px;font-size:14px;font-weight:700;color:#0d9488">{escape(ticket_ref)}</td></tr>'
        ) + detail_rows

    details_card = f"""
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;margin:0 0 28px">
            <tr><td style="padding:14px 16px 0;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#6b7280">Your submission</td></tr>
            {detail_rows}
          </table>""" if detail_rows else ''

    steps_html = ''.join(
        f"""
          <tr>
            <td width="32" valign="top" style="padding:0 12px 20px 0">
              <table cellpadding="0" cellspacing="0"><tr><td width="24" height="24" align="center" valign="middle"
                style="background:{_BRAND_GRADIENT};border-radius:50%;color:#fff;font-size:12px;font-weight:700">{i}</td></tr></table>
            </td>
            <td valign="top" style="padding:0 0 20px;font-size:14px;color:#374151;line-height:1.5">{escape(step)}</td>
          </tr>"""
        for i, step in enumerate(cfg['steps'], start=1)
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#eef2f5;font-family:-apple-system,Segoe UI,Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f5;padding:40px 0">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:20px;overflow:hidden;box-shadow:0 10px 40px -12px rgba(15,23,42,.15)">
        <tr><td style="background:#ffffff;padding:32px 32px 16px;text-align:center">
          <img src="{_logo_data_uri()}" alt="Internet Assist" width="180" style="display:inline-block;height:auto;max-width:180px">
        </td></tr>
        <tr><td style="background:{_BRAND_GRADIENT};padding:36px 32px;text-align:center">
          <table cellpadding="0" cellspacing="0" style="margin:0 auto 16px">
            <tr><td width="56" height="56" align="center" valign="middle" style="background:rgba(255,255,255,.2);border-radius:50%">
              <span style="font-size:28px;color:#fff;line-height:1">&#10003;</span>
            </td></tr>
          </table>
          <h1 style="margin:0;color:#fff;font-size:23px;font-weight:800">{escape(cfg['title'])}</h1>
        </td></tr>
        <tr><td style="padding:32px">
          <p style="margin:0 0 16px;color:#111827;font-size:15px">Hi {first},</p>
          <p style="margin:0 0 24px;color:#374151;font-size:15px;line-height:1.6">{cfg['intro']}</p>
          {details_card}
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px">
            <tr><td style="padding:0 0 4px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#0d9488">What happens next</td></tr>
          </table>
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px">
            {steps_html}
          </table>
          <table cellpadding="0" cellspacing="0" style="margin:8px 0 28px">
            <tr><td style="background:#f0fdfa;border:1px solid #99f6e4;border-radius:10px;padding:10px 16px">
              <span style="font-size:13px;color:#0f766e;font-weight:600">Expected response: {escape(cfg['eta'])}</span>
            </td></tr>
          </table>
          <table cellpadding="0" cellspacing="0">
            <tr><td style="background:{_BRAND_GRADIENT};border-radius:999px">
              <a href="tel:01621840014" style="display:inline-block;padding:13px 28px;color:#fff;font-size:14px;font-weight:700;text-decoration:none">Call us: 01621 840014</a>
            </td></tr>
          </table>
        </td></tr>
        <tr><td style="padding:20px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;text-align:center">
          <p style="margin:0 0 4px;font-size:12px;color:#9ca3af">
            Internet Assist Limited · Maldon, Essex · Cyber Essentials Certified · ISO 9001
          </p>
          <p style="margin:0;font-size:12px;color:#9ca3af">
            <a href="mailto:enquiries@ia.uk" style="color:#0d9488;text-decoration:none">enquiries@ia.uk</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

    detail_lines = "".join(f"{k}: {v}\n" for k, v in (details or {}).items() if v)
    steps_lines = "".join(f"{i}. {step}\n" for i, step in enumerate(cfg['steps'], start=1))
    plain = (
        f"Hi {recipient_name},\n\n"
        f"{cfg['intro']}\n\n"
        + (f"Your reference: {ticket_ref}\n" if ticket_ref else "")
        + (f"\nYour submission:\n{detail_lines}" if detail_lines else "")
        + f"\nWhat happens next:\n{steps_lines}"
        + f"\nExpected response: {cfg['eta']}\n\n"
        "For urgent enquiries call 01621 840014 or email enquiries@ia.uk.\n\n"
        "— Internet Assist"
    )

    try:
        _graph_send(to=[recipient_email], subject=cfg['subject'], html=html, plain=plain)
        logger.info('confirmation_email_sent', recipient=recipient_email, ticket_type=ticket_type, ticket_ref=ticket_ref)
        return True
    except Exception:
        logger.exception('confirmation_email_failed', recipient=recipient_email, ticket_type=ticket_type)
        return False


_STATUS_UPDATE_CONFIG: dict[str, dict] = {
    'reviewed': {
        'colour':  '#2563eb',
        'heading': 'Application Under Review',
        'intro':   'We\'ve completed an initial review of your application for <strong>{position}</strong> and it has caught our attention.',
        'body':    'We\'ll be in touch soon with a further update. In the meantime, if you have any questions please don\'t hesitate to contact us.',
        'subject': 'Your application is under review — Internet Assist',
    },
    'shortlisted': {
        'colour':  '#059669',
        'heading': 'Great News — You\'ve Been Shortlisted!',
        'intro':   'Congratulations! Following a review of your application for <strong>{position}</strong>, we\'d love to find out more about you.',
        'body':    'A member of our HR team will be in touch shortly to arrange the next steps. Please keep an eye on your inbox.',
        'subject': 'You\'ve been shortlisted — Internet Assist',
    },
    'rejected': {
        'colour':  '#6b7280',
        'heading': 'Application Update',
        'intro':   'Thank you for your interest in the <strong>{position}</strong> role at Internet Assist.',
        'body':    'After careful consideration we have decided not to progress your application at this time. We genuinely appreciate the time you invested and wish you every success in your search.',
        'subject': 'Your application update — Internet Assist',
    },
}


def send_job_status_update(
    recipient_email: str,
    recipient_name: str,
    position: str,
    new_status: str,
) -> bool:
    cfg = _STATUS_UPDATE_CONFIG.get(new_status)
    if not cfg:
        return True  # no email for 'new' or unknown statuses

    if not _graph_configured():
        logger.info('email_skipped_no_credentials', action='job_status_update', status=new_status)
        return True

    first   = escape(recipient_name.split()[0] if recipient_name else 'there')
    colour  = cfg['colour']
    intro   = cfg['intro'].format(position=escape(position))
    body    = cfg['body']
    subject = cfg['subject']

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">
        {_email_header(cfg['heading'])}
        <tr><td style="padding:32px">
          <p style="margin:0 0 16px;color:#111827">Hi {first},</p>
          <p style="margin:0 0 20px;color:#374151">{intro}</p>
          <p style="margin:0 0 24px;color:#374151">{body}</p>
          <p style="margin:0;color:#374151">
            You can reach us on <strong>01621 840014</strong> or at
            <a href="mailto:enquiries@ia.uk" style="color:{colour}">enquiries@ia.uk</a>.
          </p>
        </td></tr>
        <tr><td style="padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb">
          <p style="margin:0;font-size:12px;color:#9ca3af">
            Internet Assist Limited · Maldon, Essex · Cyber Essentials Certified · ISO 9001
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

    plain = (
        f"Hi {recipient_name},\n\n"
        f"{cfg['intro'].format(position=position)}\n\n"
        f"{cfg['body']}\n\n"
        "Contact us: 01621 840014 | enquiries@ia.uk\n\n"
        "— Internet Assist"
    )

    try:
        _graph_send(to=[recipient_email], subject=subject, html=html, plain=plain)
        logger.info('job_status_email_sent', recipient=recipient_email, status=new_status, position=position)
        return True
    except Exception:
        logger.exception('job_status_email_failed', recipient=recipient_email, status=new_status)
        return False


def send_ticket(ticket_type: str, ticket_id: str, fields: dict, user_email: str | None = None) -> bool:
    if not file_settings.get('enquiry_forwarding').get('enabled', True):
        logger.info('email_skipped_forwarding_disabled', ticket_type=ticket_type, ticket_id=ticket_id)
        return True

    recipients = _recipients()
    if not recipients:
        logger.info('email_skipped_no_recipients', ticket_type=ticket_type, ticket_id=ticket_id)
        return True

    if not _graph_configured():
        logger.info('email_skipped_no_credentials', ticket_type=ticket_type)
        return True

    label   = _TICKET_LABELS.get(ticket_type, 'New Request')
    short   = ticket_id[:8].upper()
    subject = f'[#{short}] New {label} — Internet Assist'

    try:
        _graph_send(
            to=recipients,
            subject=subject,
            html=_build_html(ticket_type, ticket_id, fields),
            plain=_build_plain(ticket_type, ticket_id, fields),
            reply_to=user_email,
        )
        logger.info('email_sent', subject=subject, recipients=recipients)
        return True
    except Exception:
        logger.exception('email_failed', subject=subject, recipients=recipients)
        return False


def send_ticket_with_attachments(
    ticket_type: str,
    ticket_id: str,
    fields: dict,
    user_email: str | None = None,
    attachments: list[str | tuple[bytes, str]] | None = None,
) -> bool:
    if not file_settings.get('enquiry_forwarding').get('enabled', True):
        logger.info('email_skipped_forwarding_disabled', ticket_type=ticket_type, ticket_id=ticket_id)
        return True

    recipients = _recipients()
    if not recipients:
        logger.info('email_skipped_no_recipients', ticket_type=ticket_type, ticket_id=ticket_id)
        return True

    if not _graph_configured():
        logger.info('email_skipped_no_credentials', ticket_type=ticket_type)
        return True

    label   = _TICKET_LABELS.get(ticket_type, 'New Request')
    short   = ticket_id[:8].upper()
    subject = f'[#{short}] New {label} — Internet Assist'

    graph_attachments: list[tuple[bytes, str]] = []
    if attachments:
        for item in attachments:
            try:
                if isinstance(item, tuple):
                    graph_attachments.append(item)
                else:
                    p = Path(item)
                    if not p.exists():
                        logger.warning('attachment_missing', path=item)
                        continue
                    graph_attachments.append((p.read_bytes(), p.name))
            except Exception:
                logger.exception('attachment_add_failed', item=str(item))

    try:
        _graph_send(
            to=recipients,
            subject=subject,
            html=_build_html(ticket_type, ticket_id, fields),
            plain=_build_plain(ticket_type, ticket_id, fields),
            reply_to=user_email,
            attachments=graph_attachments or None,
        )
        logger.info('email_sent', subject=subject, recipients=recipients)
        return True
    except Exception:
        logger.exception('email_failed', subject=subject, recipients=recipients)
        return False
