from __future__ import annotations

from flask import Blueprint, Response, abort

from app.services.media_service import load_image

blp = Blueprint('media', __name__)


@blp.route('/media/projects/<path:file_name>')
def serve_project_image(file_name: str):
    """Decrypt and stream a project image. Public — no auth required."""
    # Reject path traversal attempts
    if '/' in file_name or '..' in file_name:
        abort(400)

    result = load_image(file_name)
    if result is None:
        abort(404)

    data, content_type = result
    return Response(
        data,
        status=200,
        headers={
            'Content-Type': content_type,
            'Cache-Control': 'public, max-age=86400',
            'Content-Length': str(len(data)),
        },
    )


@blp.route('/media/blog/<path:file_name>')
def serve_blog_image(file_name: str):
    """Decrypt and stream a blog cover image. Public — no auth required."""
    if '/' in file_name or '..' in file_name:
        abort(400)

    result = load_image(file_name)
    if result is None:
        abort(404)

    data, content_type = result
    return Response(
        data,
        status=200,
        headers={
            'Content-Type': content_type,
            'Cache-Control': 'public, max-age=86400',
            'Content-Length': str(len(data)),
        },
    )
