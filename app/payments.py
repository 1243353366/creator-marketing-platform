"""Payment webhook boundary. No real provider is configured by
default and NO transaction is ever faked. The webhook receiver
verifies HMAC signatures with replay protection and idempotency;
without the secret it fails closed."""
import hashlib
import hmac
import json
import time

from .db import session
from .models import AuditLog, WebhookEvent

REPLAY_WINDOW = 300


class WebhookRefused(Exception):
    pass


def verify_webhook(app_cfg, provider, event_id, timestamp,
                   payload_bytes, signature_hex):
    """Fail-closed: bad signature, stale timestamp, missing
    secret, or a replayed event all raise WebhookRefused."""
    secret = app_cfg.get("PAYMENT_WEBHOOK_SECRET", "")
    if not secret:
        raise WebhookRefused("payment provider not configured")
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        raise WebhookRefused("bad timestamp") from None
    if abs(time.time() - ts) > REPLAY_WINDOW:
        raise WebhookRefused("stale webhook")
    mac = hmac.new(secret.encode(),
                   payload_bytes, hashlib.sha256)
    if not hmac.compare_digest(
            mac.hexdigest(), str(signature_hex or "")):
        raise WebhookRefused("bad signature")
    db = session()
    row = db.query(WebhookEvent).filter(
        WebhookEvent.provider == provider,
        WebhookEvent.event_id == event_id).one_or_none()
    if row is not None:
        raise WebhookRefused("duplicate event (already "
                             "processed; idempotent no-op)")
    db.add(WebhookEvent(
        provider=provider, event_id=event_id,
        payload_hash=hashlib.sha256(
            payload_bytes).hexdigest(), verified=True))
    db.add(AuditLog(user_id="-", action="webhook_accepted",
                    target=f"{provider}:{event_id}"))
    db.commit()
    return True


def provider_status(app_cfg):
    """Honest configuration status for the UI."""
    configured = bool(app_cfg.get("PAYMENT_WEBHOOK_SECRET"))
    return {
        "configured": configured,
        "note": ("Live provider integration requires provider "
                  "terms that permit this business model plus "
                  "account approval. Until then this page is a "
                  "clearly-labeled boundary, not a fake "
                  "checkout.")}
