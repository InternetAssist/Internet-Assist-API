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
        'colour':  '#059669',
        'title':   'Application Received',
        'intro':   'Thank you for applying to Internet Assist! We\'ve received your CV and will review your application carefully.',
        'body':    'Our HR team will be in touch within <strong>5 working days</strong> if your profile matches our requirements.',
        'subject': 'We\'ve received your application — Internet Assist',
    },
    'contact': {
        'colour':  '#2563eb',
        'title':   'Message Received',
        'intro':   'Thank you for getting in touch with Internet Assist!',
        'body':    'A member of our team will respond to your enquiry within <strong>1 working day</strong>.',
        'subject': 'We\'ve received your message — Internet Assist',
    },
    'quote': {
        'colour':  '#7c3aed',
        'title':   'Quote Request Received',
        'intro':   'Thank you for requesting a quote from Internet Assist!',
        'body':    'Our team will review your requirements and prepare a tailored proposal, usually within <strong>2 working days</strong>.',
        'subject': 'We\'ve received your quote request — Internet Assist',
    },
    'remote_support': {
        'colour':  '#dc2626',
        'title':   'Remote Support Request Received',
        'intro':   'Thank you for reaching out to Internet Assist for remote support!',
        'body':    'A technician will pick up your request shortly, usually within <strong>30 minutes</strong> during business hours.',
        'subject': 'We\'ve received your support request — Internet Assist',
    },
}


def send_confirmation(
    ticket_type: str,
    recipient_email: str,
    recipient_name: str,
    ticket_ref: str | None = None,
) -> bool:
    if not _graph_configured():
        logger.info('email_skipped_no_credentials', action='confirmation', ticket_type=ticket_type)
        return True

    cfg = _CONFIRMATION_CONFIG.get(ticket_type)
    if not cfg:
        return False

    first = escape(recipient_name.split()[0] if recipient_name else 'there')
    ref_row = (
        f'<tr><td style="padding:8px 12px;font-weight:600;color:#374151;background:#f9fafb;border-bottom:1px solid #e5e7eb;width:30%">Reference</td>'
        f'<td style="padding:8px 12px;color:#111827;border-bottom:1px solid #e5e7eb;font-weight:700">{escape(ticket_ref)}</td></tr>'
        if ticket_ref else ''
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">
        {_email_header(cfg['title'])}
        <tr><td style="padding:32px">
          <p style="margin:0 0 16px;color:#111827">Hi {first},</p>
          <p style="margin:0 0 20px;color:#374151">{cfg['intro']}</p>
          <p style="margin:0 0 24px;color:#374151">{cfg['body']}</p>
          {f'<table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:6px;overflow:hidden;margin-bottom:24px">{ref_row}</table>' if ref_row else ''}
          <p style="margin:0;color:#374151">
            In the meantime, feel free to call us on <strong>01621 840014</strong> or email
            <a href="mailto:enquiries@ia.uk" style="color:{cfg['colour']}">enquiries@ia.uk</a>.
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
        f"{cfg['intro']}\n\n"
        + (f"Your reference: {ticket_ref}\n\n" if ticket_ref else "")
        + "For urgent enquiries call 01621 840014 or email enquiries@ia.uk.\n\n"
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
