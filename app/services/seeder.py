from __future__ import annotations

from app.extensions import db
from app.logging import logger
from app.models.role import Role

SEED_ROLES = ['admin', 'staff']


def _seed_roles() -> None:
    added = []
    for name in SEED_ROLES:
        if not Role.query.filter_by(name=name).first():
            db.session.add(Role(name=name))
            added.append(name)
    db.session.flush()
    if added:
        logger.info('roles_seeded', roles=added)
    else:
        logger.info('roles_already_exist')


def run_seed() -> None:
    db.create_all()
    _seed_roles()
    db.session.commit()
    logger.info('seed_complete')
