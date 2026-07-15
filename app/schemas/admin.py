from __future__ import annotations

from marshmallow import EXCLUDE, Schema, fields, validate

from app.models.project import SERVICE_TYPES

from .base import BaseSchema


class PaginationQuerySchema(BaseSchema):
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    page_size = fields.Integer(load_default=25, validate=validate.Range(min=1, max=100))
    status = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=30))
    q = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=200))


class StatsResponseSchema(BaseSchema):
    contacts = fields.Dict(required=True)
    quotes = fields.Dict(required=True)
    jobs = fields.Dict(required=True)
    totals = fields.Dict(required=True)


class PatchStatusSchema(BaseSchema):
    status = fields.String(required=True, validate=validate.Length(min=1, max=30))


class ProjectCreateSchema(BaseSchema):
    title     = fields.String(required=True, validate=validate.Length(min=2, max=255))
    client    = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=255))
    industry  = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=100))
    summary   = fields.String(required=True, validate=validate.Length(min=10))
    challenge = fields.String(load_default=None, allow_none=True)
    solution  = fields.String(load_default=None, allow_none=True)
    outcome   = fields.String(load_default=None, allow_none=True)
    tags        = fields.List(fields.String(), load_default=list)
    service_type = fields.String(required=True, validate=validate.OneOf(SERVICE_TYPES))
    image_url   = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=1024))
    project_url = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=1024))
    status      = fields.String(load_default=None, allow_none=True, validate=validate.OneOf(['draft', 'published']))


class ProjectPatchSchema(Schema):
    """Lenient schema for PATCH /admin/projects/:id.
    All fields optional, no strict min-lengths, unknown keys are silently dropped."""
    class Meta:
        unknown = EXCLUDE

    title     = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=255))
    client    = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=255))
    industry  = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=100))
    summary   = fields.String(load_default=None, allow_none=True)
    challenge = fields.String(load_default=None, allow_none=True)
    solution  = fields.String(load_default=None, allow_none=True)
    outcome   = fields.String(load_default=None, allow_none=True)
    tags        = fields.List(fields.String(), load_default=None, allow_none=True)
    service_type = fields.String(load_default=None, allow_none=True, validate=validate.OneOf(SERVICE_TYPES))
    image_url   = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=1024))
    project_url = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=1024))
    status      = fields.String(load_default=None, allow_none=True, validate=validate.OneOf(['draft', 'published']))


class BlogPostCreateSchema(BaseSchema):
    title       = fields.String(required=True, validate=validate.Length(min=2, max=255))
    slug        = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=255))
    excerpt     = fields.String(load_default=None, allow_none=True)
    body        = fields.String(required=True, validate=validate.Length(min=10))
    author_name = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=150))
    tags        = fields.List(fields.String(), load_default=list)
    cover_image_url = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=1024))
    status      = fields.String(load_default=None, allow_none=True, validate=validate.OneOf(['draft', 'published']))


class BlogPostPatchSchema(Schema):
    """Lenient schema for PATCH /admin/blog/:id.
    All fields optional, no strict min-lengths, unknown keys are silently dropped."""
    class Meta:
        unknown = EXCLUDE

    title       = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=255))
    slug        = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=255))
    excerpt     = fields.String(load_default=None, allow_none=True)
    body        = fields.String(load_default=None, allow_none=True)
    author_name = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=150))
    tags        = fields.List(fields.String(), load_default=None, allow_none=True)
    cover_image_url = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=1024))
    status      = fields.String(load_default=None, allow_none=True, validate=validate.OneOf(['draft', 'published']))


class CompanyCreateSchema(BaseSchema):
    name  = fields.String(required=True, validate=validate.Length(min=2, max=255))
    notes = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=2000))


class CompanyPatchSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    name  = fields.String(load_default=None, allow_none=True, validate=validate.Length(min=2, max=255))
    notes = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=2000))


class CompanyFileUploadSchema(BaseSchema):
    description = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=500))


class CompanyFilePatchSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    description = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=500))


class JobPostingCreateSchema(BaseSchema):
    title           = fields.String(required=True, validate=validate.Length(min=2, max=255))
    team            = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=100))
    location        = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=255))
    type            = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=100))
    summary         = fields.String(load_default=None, allow_none=True)
    responsibilities = fields.List(fields.String(), load_default=list)
    requirements    = fields.List(fields.String(), load_default=list)
    status          = fields.String(load_default=None, allow_none=True, validate=validate.OneOf(['active', 'inactive', 'closed']))
