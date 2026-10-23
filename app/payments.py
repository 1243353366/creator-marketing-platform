import datetime
"""Payment webhook boundary. No real provider is configured by
default and NO transaction is ever faked. The webhook receiver
verifies HMAC signatures with replay protection and idempotency;
without the secret it fails closed."""
import hashlib
import hmac
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


# ---------------------------------------------------------------------------
# Payment abstraction layer
#
# Three SEPARATE flows with SEPARATE provider rules:
#   SAAS_SUBSCRIPTION: fans are NOT here. This is the creator paying
#     for marketing software. A standard software processor (Stripe,
#     card, wallet) suits this flow; the SaaS sells software, not
#     adult content.
#   CREATOR_CHECKOUT: a fan paying a creator for content. This needs
#     a processor that EXPLICITLY permits the creator's content
#     category (Segpay/CCBill/Verotel candidates - written approval
#     required for the exact model). Cash App prohibits adult
#     content; PayPal prohibits sexually oriented digital content;
#     Stripe restricts adult sales. A payment handle is not a
#     verified payment.
#   CREATOR_PAYOUT: paying a creator their own revenue. Separate
#     provider rules again; we do not collect or hold creator funds
#     (that would be a marketplace model needing written approval).
# ---------------------------------------------------------------------------

SAAS_SUBSCRIPTION = "saas_subscription"
CREATOR_CHECKOUT = "creator_checkout"
CREATOR_PAYOUT = "creator_payout"
FLOWS = (SAAS_SUBSCRIPTION, CREATOR_CHECKOUT, CREATOR_PAYOUT)

# Which flows a provider MAY be configured for, per published
# policies AS OF 2026-10 (re-verify at integration time; this table
# is a guardrail, not legal advice). A provider blocked for a flow
# is refused structurally, not just by convention.
PROVIDER_FLOW_POLICY = {
    "stripe": {SAAS_SUBSCRIPTION, CREATOR_PAYOUT},
    # Stripe restricts adult-content sales: never CREATOR_CHECKOUT
    # for adult creators.
    "segpay": {CREATOR_CHECKOUT, CREATOR_PAYOUT},
    "ccbill": {CREATOR_CHECKOUT, CREATOR_PAYOUT},
    "verotel": {CREATOR_CHECKOUT, CREATOR_PAYOUT},
    # verify-first candidates: confirm availability, account
    # eligibility, fees, and country coverage in writing before
    # configuring (see docs/PAYMENTS.md)
    "paxum": {CREATOR_CHECKOUT, CREATOR_PAYOUT},
    "skrill": {CREATOR_CHECKOUT, CREATOR_PAYOUT},
    "cash_app": set(),     # prohibits adult content and related
    "paypal": {SAAS_SUBSCRIPTION},  # prohibits adult digital
    # content; even for the SaaS flow, prefer dedicated software
    # processors.
    "venmo": set(),
    "wise": {CREATOR_PAYOUT},   # verify permitted purpose
}


class AdapterError(Exception):
    """Raised when no provider is configured for a flow. The
    boundary fails closed: no checkout is created, nothing is
    faked."""


class _Adapter:
    flow = None
    provider = None

    def create_checkout(self, tenant_id, amount_cents,
                        metadata):
        raise AdapterError(
            f"no provider configured for flow {self.flow!r}; "
            "setup instructions: docs/PAYMENTS.md")

    def verify_payment(self, reference):
        raise AdapterError(
            f"no provider configured for flow {self.flow!r}")


_ADAPTERS = {}


def configure_provider(flow, provider, app_cfg):
    """Bind a provider to a flow if (a) its policy permits that
    flow and (b) the required credentials exist. Fail closed on
    both counts."""
    if flow not in FLOWS:
        raise AdapterError(f"unknown flow {flow!r}")
    allowed = PROVIDER_FLOW_POLICY.get(provider, set())
    if flow not in allowed:
        raise AdapterError(
            f"provider {provider!r} is not permitted for flow "
            f"{flow!r} under its published policy (see "
            "docs/PAYMENTS.md)")
    if not app_cfg.get(f"PAYMENT_{provider.upper()}_SECRET"):
        raise AdapterError(
            f"PAYMENT_{provider.upper()}_SECRET is not "
            "configured; refusing to fake the integration")
    ad = _Adapter()
    ad.flow = flow
    ad.provider = provider
    _ADAPTERS[flow] = ad
    return ad


def adapter_for(flow):
    """The configured adapter for a flow, or an unconfigured
    boundary that refuses everything."""
    if flow not in FLOWS:
        raise AdapterError(f"unknown flow {flow!r}")
    if flow in _ADAPTERS:
        return _ADAPTERS[flow]
    un = _Adapter()
    un.flow = flow
    return un


def record_verified_payment(flow, tenant_id, reference,
                           provider):
    """Only a server-verified payment creates a row here. Content
    unlocks require this row; a submitted handle or screenshot
    never does."""
    from .db import session
    from .models import VerifiedPayment
    db = session()
    row = db.query(VerifiedPayment).filter_by(
        flow=flow, tenant_id=tenant_id,
        reference=reference).one_or_none()
    if row is not None:
        return row  # idempotent webhook replays
    row = VerifiedPayment(flow=flow, tenant_id=tenant_id,
                          reference=reference,
                          provider=provider,
                          verified_at=datetime.datetime.now(
                              datetime.timezone.utc))
    db.add(row)
    db.commit()
    return row


def payment_is_verified(flow, tenant_id, reference):
    """The ONLY gate for unlocking paid access."""
    from .db import session
    from .models import VerifiedPayment
    return session().query(VerifiedPayment).filter_by(
        flow=flow, tenant_id=tenant_id,
        reference=reference).one_or_none() is not None
