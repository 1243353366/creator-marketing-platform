"""Consent: analytics and training are SEPARATE grants. Analytics
queries only run on consented users; training use requires its own
grant and is never implied by analytics consent."""
from .db import session
from .models import Consent, Metric


def set_consent(user_id, kind, granted):
    if kind not in ("analytics", "training"):
        raise ValueError("unknown consent kind")
    db = session()
    row = db.query(Consent).filter(Consent.user_id == user_id,
                                   Consent.kind == kind).one_or_none()
    if row is None:
        row = Consent(user_id=user_id, kind=kind, granted=granted)
        db.add(row)
    else:
        row.granted = granted
    if kind == "analytics" and not granted:
        # withdrawal: analytics rows for this tenant are deleted.
        db.query(Metric).filter(
            Metric.user_id == user_id).delete()
    db.commit()
    return row


def has_consent(user_id, kind):
    db = session()
    row = db.query(Consent).filter(
        Consent.user_id == user_id,
        Consent.kind == kind).one_or_none()
    return bool(row and row.granted)
