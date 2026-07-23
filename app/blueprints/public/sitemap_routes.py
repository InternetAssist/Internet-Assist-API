from __future__ import annotations

from xml.sax.saxutils import escape

from flask import Response
from flask_smorest import Blueprint

from app.models.blog_post import BlogPost

blp = Blueprint('public-sitemap', __name__, description='Dynamic sitemap')

SITE_URL = 'https://www.ia.uk'

# Hand-maintained marketing pages — these change rarely, unlike blog posts,
# so there's no need to generate them from the frontend route table.
_STATIC_URLS = [
    ('/', 'weekly', '1.0'),
    ('/about', 'monthly', '0.8'),
    ('/services', 'monthly', '0.9'),
    ('/it-support', 'monthly', '0.95'),
    ('/cloud-services', 'monthly', '0.95'),
    ('/cyber-security', 'monthly', '0.95'),
    ('/backup-recovery', 'monthly', '0.9'),
    ('/communications', 'monthly', '0.9'),
    ('/infrastructure', 'monthly', '0.9'),
    ('/software-development', 'monthly', '0.9'),
    ('/web-design', 'monthly', '0.9'),
    ('/remote-support', 'monthly', '0.8'),
    ('/quote', 'monthly', '0.7'),
    ('/contact', 'monthly', '0.7'),
    ('/careers', 'weekly', '0.6'),
    ('/projects', 'monthly', '0.7'),
    ('/privacy-policy', 'yearly', '0.3'),
    ('/modern-slavery', 'yearly', '0.3'),
    ('/it-support-chelmsford', 'monthly', '0.6'),
    ('/it-support-witham', 'monthly', '0.6'),
    ('/it-support-braintree', 'monthly', '0.6'),
    ('/it-support-burnham-on-crouch', 'monthly', '0.6'),
    ('/it-support-south-woodham-ferrers', 'monthly', '0.6'),
    ('/it-support-tiptree', 'monthly', '0.6'),
    ('/it-support-colchester', 'monthly', '0.6'),
    ('/it-support-billericay', 'monthly', '0.6'),
    ('/it-support-basildon', 'monthly', '0.6'),
    ('/it-support-southend-on-sea', 'monthly', '0.6'),
    ('/it-support-wickford', 'monthly', '0.6'),
    ('/it-support-tollesbury', 'monthly', '0.6'),
    ('/it-support-dengie', 'monthly', '0.6'),
    ('/it-support-great-dunmow', 'monthly', '0.6'),
    ('/it-support-sudbury', 'monthly', '0.6'),
    ('/it-support-london', 'monthly', '0.6'),
    ('/it-support-upminster', 'monthly', '0.6'),
    ('/it-support-hornchurch', 'monthly', '0.6'),
    ('/it-support-romford', 'monthly', '0.6'),
    ('/it-support-brentwood', 'monthly', '0.6'),
    ('/it-support-shenfield', 'monthly', '0.6'),
    # '/blog' hub intentionally omitted until there's published content —
    # an empty index page isn't worth submitting for indexing yet.
]


@blp.route('/sitemap.xml', methods=['GET'])
def sitemap():
    entries = [
        f"<url><loc>{escape(SITE_URL + path)}</loc><changefreq>{freq}</changefreq><priority>{priority}</priority></url>"
        for path, freq, priority in _STATIC_URLS
    ]

    posts = BlogPost.query.filter_by(status='published').order_by(BlogPost.published_at.desc()).all()
    for post in posts:
        lastmod = (post.updated_at or post.published_at).date().isoformat()
        entries.append(
            f"<url><loc>{escape(SITE_URL + '/blog/' + post.slug)}</loc>"
            f"<lastmod>{lastmod}</lastmod><changefreq>monthly</changefreq><priority>0.6</priority></url>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + ''.join(entries) +
        '</urlset>'
    )
    return Response(xml, status=200, mimetype='application/xml')
