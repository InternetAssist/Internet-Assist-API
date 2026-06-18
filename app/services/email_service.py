from __future__ import annotations

import base64
import mimetypes
from html import escape
from pathlib import Path

import requests
from flask import current_app

from app.logging import logger

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
        <tr>
          <td style="background:{colour};padding:24px 32px">
            <p style="margin:0;color:rgba(255,255,255,.8);font-size:12px;text-transform:uppercase;letter-spacing:1px">Internet Assist</p>
            <h1 style="margin:4px 0 0;color:#fff;font-size:22px">{escape(label)}</h1>
          </td>
        </tr>
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


def send_otp(recipient: str, full_name: str, code: str) -> bool:
    if not _graph_configured():
        logger.info('email_skipped_no_credentials', action='otp')
        return True

    first = escape(full_name.split()[0] if full_name else 'there')
    subject = 'Your Internet Assist sign-in code'
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">
        <tr><td style="background:#2563eb;padding:24px 32px">
          <p style="margin:0;color:rgba(255,255,255,.8);font-size:12px;text-transform:uppercase;letter-spacing:1px">Internet Assist</p>
          <h1 style="margin:4px 0 0;color:#fff;font-size:22px">Sign-in verification</h1>
        </td></tr>
        <tr><td style="padding:32px">
          <p style="margin:0 0 16px;color:#111827">Hi {first},</p>
          <p style="margin:0 0 24px;color:#374151">Enter the code below to complete your sign-in. It expires in <strong>10 minutes</strong>.</p>
          <div style="text-align:center;margin:0 0 28px">
            <span style="display:inline-block;background:#f1f5f9;border:2px solid #e2e8f0;border-radius:12px;padding:20px 40px;font-size:36px;font-weight:800;letter-spacing:10px;color:#1e293b;font-family:monospace">{escape(code)}</span>
          </div>
          <p style="margin:0;font-size:12px;color:#9ca3af">
            If you did not attempt to sign in, someone may have your password — change it immediately.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    plain = (
        f"Hi {full_name},\n\nYour Internet Assist sign-in code is: {code}\n\n"
        "It expires in 10 minutes.\n\n"
        "If you did not attempt to sign in, change your password immediately."
    )

    try:
        _graph_send(to=[recipient], subject=subject, html=html, plain=plain)
        logger.info('otp_email_sent', recipient=recipient)
        return True
    except Exception:
        logger.exception('otp_email_failed', recipient=recipient)
        return False


def send_password_reset(recipient: str, full_name: str, reset_url: str) -> bool:
    if not _graph_configured():
        logger.info('email_skipped_no_credentials', action='password_reset')
        return True

    first = escape(full_name.split()[0] if full_name else 'there')
    safe_url = escape(reset_url)
    subject = 'Reset your Internet Assist password'
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">
        <tr><td style="background:#2563eb;padding:24px 32px">
          <p style="margin:0;color:rgba(255,255,255,.8);font-size:12px;text-transform:uppercase;letter-spacing:1px">Internet Assist</p>
          <h1 style="margin:4px 0 0;color:#fff;font-size:22px">Password Reset</h1>
        </td></tr>
        <tr><td style="padding:32px">
          <p style="margin:0 0 16px;color:#111827">Hi {first},</p>
          <p style="margin:0 0 24px;color:#374151">We received a request to reset your admin password. Click the button below — the link expires in <strong>1 hour</strong>.</p>
          <a href="{safe_url}"
             style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;padding:12px 28px;border-radius:6px;font-weight:600;font-size:15px">
            Reset Password
          </a>
          <p style="margin:24px 0 0;font-size:12px;color:#9ca3af">
            If you didn't request this, ignore this email — your password won't change.<br>
            Or copy this link: {safe_url}
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    plain = (
        f"Hi {full_name},\n\nReset your Internet Assist admin password:\n{reset_url}\n\n"
        "This link expires in 1 hour.\n\nIf you didn't request this, ignore this email."
    )

    try:
        _graph_send(to=[recipient], subject=subject, html=html, plain=plain)
        logger.info('password_reset_email_sent', recipient=recipient)
        return True
    except Exception:
        logger.exception('password_reset_email_failed', recipient=recipient)
        return False


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
        <tr><td style="background:{cfg['colour']};padding:24px 32px">
          <p style="margin:0;color:rgba(255,255,255,.8);font-size:12px;text-transform:uppercase;letter-spacing:1px">Internet Assist</p>
          <h1 style="margin:4px 0 0;color:#fff;font-size:22px">{escape(cfg['title'])}</h1>
        </td></tr>
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
    heading = escape(cfg['heading'])
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
        <tr><td style="background:{colour};padding:24px 32px">
          <p style="margin:0;color:rgba(255,255,255,.8);font-size:12px;text-transform:uppercase;letter-spacing:1px">Internet Assist</p>
          <h1 style="margin:4px 0 0;color:#fff;font-size:22px">{heading}</h1>
        </td></tr>
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
