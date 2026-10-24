"""Schema. Every tenant-owned row carries user_id; repositories
always filter on it (see repo.py)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (Boolean, CheckConstraint, DateTime,
                        ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _uid():
    return uuid.uuid4().hex


def _now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                    default=_uid)
    email: Mapped[str] = mapped_column(String(255), nullable=False,
                                       unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64),
                                            primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                         default=_now, index=True)


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    granted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)
    __table_args__ = (
        CheckConstraint(kind.in_(["analytics", "training"]),
                        name="ck_consent_kind"),
        UniqueConstraint("user_id", "kind",
                         name="uq_consent_user_kind"),
    )


class BrandProfile(Base):
    __tablename__ = "brand_profiles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                    default=_uid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    voice: Mapped[str] = mapped_column(String(255))
    audience: Mapped[str] = mapped_column(Text)
    intensity: Mapped[int] = mapped_column(Integer, default=2)
    restrictions: Mapped[str] = mapped_column(Text, default="")
    cta: Mapped[str] = mapped_column(String(255), default="")
    checkout_url: Mapped[str] = mapped_column(String(500),
                                              nullable=True)
    business_url: Mapped[str] = mapped_column(String(500),
                                              nullable=True)
    conversion_goal: Mapped[str] = mapped_column(String(255),
                                                 default="")
    channels: Mapped[str] = mapped_column(String(255), default="")
    __table_args__ = (
        CheckConstraint("intensity between 1 and 5",
                        name="ck_intensity"),
    )


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                    default=_uid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    channel: Mapped[str] = mapped_column(String(32))
    objective: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)
    __table_args__ = (
        CheckConstraint(status.in_(
            ["draft", "generated", "confirmed", "published"]),
            name="ck_campaign_status"),
    )


class Variant(Base):
    __tablename__ = "variants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                    default=_uid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(4))
    content_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    __table_args__ = (
        UniqueConstraint("campaign_id", "label",
                         name="uq_variant_label"),
        CheckConstraint(source.in_(
            ["provider", "template", "bridge-ai"]),
                        name="ck_variant_source"),
    )


class GenerationLog(Base):
    __tablename__ = "generation_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    # 32 chars: the ck_gen_status allowlist already promised
    # provider_unavailable (20 chars); 16 was too narrow to
    # hold statuses the constraint itself permits
    status: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                         default=_now, index=True)
    __table_args__ = (
        CheckConstraint(status.in_(
            ["ok", "provider_unavailable", "provider_timeout",
             "malformed_output", "over_budget", "rejected"]),
            name="ck_gen_status"),
    )


class Metric(Base):
    __tablename__ = "metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(32), index=True)
    variant_label: Mapped[str] = mapped_column(String(4))
    # like-for-like normalization: the settings that produced this
    # result travel WITH the row, not looked up later
    intensity: Mapped[int] = mapped_column(Integer, nullable=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True))
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    revenue_cents: Mapped[int] = mapped_column(Integer, default=0)
    spend_cents: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32))


class SeoPage(Base):
    __tablename__ = "seo_pages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                    default=_uid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    last_status: Mapped[int] = mapped_column(String(16), default="")
    last_checked: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_page_url"),
    )


class SeoAudit(Base):
    __tablename__ = "seo_audits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[str] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    http_status: Mapped[int] = mapped_column(Integer, default=0)
    redirect_ok: Mapped[bool] = mapped_column(Boolean)
    robots_ok: Mapped[bool] = mapped_column(Boolean)
    canonical_ok: Mapped[bool] = mapped_column(Boolean)
    meta_title: Mapped[str] = mapped_column(Text, default="")
    meta_desc: Mapped[str] = mapped_column(Text, default="")
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)
    __table_args__ = (
        Index("ix_audit_user_time", "user_id", "checked_at"),
    )


class CreatorFeedback(Base):
    """Creator's qualitative feedback on a variant. Optional,
    timestamped, tenant-scoped; joins the monthly analysis as
    evidence, never as a promotion trigger."""
    __tablename__ = "creator_feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(32),
                                             index=True)
    variant_label: Mapped[str] = mapped_column(String(4))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)


class MonthlyReview(Base):
    __tablename__ = "monthly_reviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                    default=_uid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    period: Mapped[str] = mapped_column(String(7))
    status: Mapped[str] = mapped_column(String(16),
                                       default="proposed")
    proposal_json: Mapped[str] = mapped_column(Text)
    transition_log: Mapped[str] = mapped_column(
        Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    __table_args__ = (
        UniqueConstraint("user_id", "period", name="uq_review"),
        CheckConstraint(status.in_(
            ["proposed", "approved", "rejected", "testing",
             "released", "rolled_back"]),
            name="ck_review_status"),
    )


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                    default=_uid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    review_id: Mapped[str] = mapped_column(String(32), index=True)
    spec_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16),
                                       default="planned")
    result_json: Mapped[str] = mapped_column(Text, default="{}")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(255), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                         default=_now, index=True)


class VerifiedPayment(Base):
    """A payment verified server-side (signed webhook or provider
    API check). The ONLY basis for unlocking paid access. A
    submitted handle or screenshot never creates a row here."""
    __tablename__ = "verified_payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow: Mapped[str] = mapped_column(String(24), index=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reference: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(32))
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)
    __table_args__ = (
        UniqueConstraint("flow", "tenant_id", "reference",
                         name="uq_verified_payment"),)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    verified: Mapped[bool] = mapped_column(Boolean)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)
    __table_args__ = (
        UniqueConstraint("provider", "event_id",
                         name="uq_webhook_event"),
    )
