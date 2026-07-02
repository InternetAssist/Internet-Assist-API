from __future__ import annotations

from dataclasses import dataclass
from functools import wraps

from flask import abort, g
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request


@dataclass
class CurrentUser:
    """The signed-in admin, resolved entirely from JWT claims set at Microsoft
    login time (see app/blueprints/admin/routes.py:microsoft_callback) --
    there's no local users table. `id` is the email; kept under that name so
    every existing `actor_user_id=g.current_user.id` audit-log call site
    didn't need to change."""

    id: str
    email: str
    full_name: str
    roles: list[str]


def roles_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            claims = get_jwt()
            user_roles = claims.get('roles', [])
            if not any(role in user_roles for role in roles):
                abort(403, description='Admin role required')
            email = get_jwt_identity()
            g.current_user = CurrentUser(
                id=email,
                email=email,
                full_name=claims.get('full_name', email),
                roles=user_roles,
            )
            return fn(*args, **kwargs)

        return wrapper

    return decorator
