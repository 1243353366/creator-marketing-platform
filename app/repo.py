"""Tenant-scoped repository helpers. Every read/write of tenant
data goes through here; user_id ALWAYS comes from the session,
never from the request."""
from .db import session


def owned(model, user_id, row_id):
    db = session()
    return db.query(model).filter(
        model.id == row_id, model.user_id == user_id
    ).one_or_none()


def save(*rows):
    db = session()
    for r in rows:
        db.add(r)
    db.commit()
    return rows
