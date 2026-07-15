from __future__ import annotations

from marshmallow import fields, validate

from .base import BaseSchema


class ChatRequestSchema(BaseSchema):
    session_id = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=36))
    message = fields.String(required=True, validate=validate.Length(min=1, max=2000))


class ChatResponseSchema(BaseSchema):
    reply = fields.String(required=True)
    type = fields.String(required=True)


# Honeypot: a field real users never see or fill (hidden off-screen in the
# frontend), so any non-empty value here means the submitter is a bot blindly
# filling every input it finds. Optional/lenient on purpose -- it must never
# reject a legitimate submission for omitting it.
_website_honeypot = fields.String(load_default='', allow_none=True, validate=validate.Length(max=255))

# reCAPTCHA v3 token -- optional here too; verify_recaptcha() itself decides
# whether a missing/failing token blocks the submission (skipped entirely
# when RECAPTCHA_SECRET_KEY isn't configured, e.g. local dev).
_recaptcha_token = fields.String(load_default='', allow_none=True, validate=validate.Length(max=4096))


class ContactCreateSchema(BaseSchema):
    name = fields.String(required=True, validate=validate.Length(min=2, max=200))
    email = fields.Email(required=True)
    phone = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=50))
    company = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=255))
    message = fields.String(required=True, validate=validate.Length(min=10, max=5000))
    website = _website_honeypot
    recaptcha_token = _recaptcha_token


class QuoteCreateSchema(BaseSchema):
    name = fields.String(required=True, validate=validate.Length(min=2, max=200))
    email = fields.Email(required=True)
    phone = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=50))
    company = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=255))
    services = fields.List(
        fields.String(validate=validate.Length(max=100)),
        required=True,
        validate=validate.Length(min=1),
    )
    team_size = fields.Integer(load_default=None, allow_none=True, validate=validate.Range(min=1, max=100000))
    timeline = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=255))
    details = fields.String(required=True, validate=validate.Length(min=10, max=5000))
    website = _website_honeypot
    recaptcha_token = _recaptcha_token


class JobApplicationFormSchema(BaseSchema):
    full_name = fields.String(required=True, data_key='fullName', validate=validate.Length(min=2, max=200))
    email = fields.Email(required=True)
    phone = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=50))
    position = fields.String(required=True, validate=validate.Length(min=2, max=255))
    cover_letter = fields.String(
        load_default=None,
        data_key='coverLetter',
        allow_none=True,
        validate=validate.Length(max=5000),
    )
    website = _website_honeypot
    recaptcha_token = _recaptcha_token


class RemoteSupportRequestSchema(BaseSchema):
    name = fields.String(required=True, validate=validate.Length(min=2, max=200))
    email = fields.Email(required=True)
    phone = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=50))
    issue = fields.String(required=True, validate=validate.Length(min=5, max=5000))
    website = _website_honeypot
    recaptcha_token = _recaptcha_token
