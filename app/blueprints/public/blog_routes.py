from __future__ import annotations

from flask import current_app, request
from flask_smorest import Blueprint

from app.models.blog_post import BlogPost
from app.utils.response import envelope

blp = Blueprint('public-blog', __name__, description='Public blog posts')


def _cover_image_url(p: BlogPost) -> str | None:
    if p.cover_image_file_id:
        host = request.host_url.split('://', 1)[-1].rstrip('/')
        scheme = 'https' if current_app.config.get('APP_ENV') == 'production' else request.scheme
        return f"{scheme}://{host}/media/blog/{p.cover_image_file_id}"
    return p.cover_image_url or None


def _serialize(p: BlogPost) -> dict:
    return {
        'id': p.id,
        'title': p.title,
        'slug': p.slug,
        'excerpt': p.excerpt,
        'body': p.body,
        'author_name': p.author_name,
        'tags': p.tags or [],
        'cover_image_url': _cover_image_url(p),
        'published_at': p.published_at.isoformat() if p.published_at else None,
        'updated_at': p.updated_at.isoformat(),
    }


@blp.route('/blog', methods=['GET'])
def list_blog_posts():
    posts = (
        BlogPost.query.filter_by(status='published')
        .order_by(BlogPost.published_at.desc())
        .all()
    )
    return envelope(data=[_serialize(p) for p in posts], status=200)


@blp.route('/blog/<string:slug>', methods=['GET'])
def get_blog_post(slug: str):
    post = BlogPost.query.filter_by(slug=slug, status='published').first()
    if not post:
        return envelope(error={'code': 'not_found', 'message': 'Blog post not found', 'details': None}, status=404)
    return envelope(data=_serialize(post), status=200)
