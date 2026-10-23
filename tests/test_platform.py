import json
import time

import pytest

from tests.conftest import csrf_of, make_user


def test_failclosed_boot():
    from app import create_app
    with pytest.raises(RuntimeError):
        create_app(config={"DATABASE_URL": "",
                           "SECRET_KEY": "x" * 20})
    with pytest.raises(RuntimeError):
        create_app(config={
            "DATABASE_URL": "sqlite:///:memory:",
            "SECRET_KEY": "x" * 20})
    with pytest.raises(RuntimeError):
        create_app(config={
            "DATABASE_URL": "postgresql://a/b",
            "SECRET_KEY": "short"})


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_login_flow_and_throttle(client):
    from app import auth
    from app.db import session
    from app.models import User
    db = session()
    db.add(User(email="a@x.com",
                password_hash=auth.hash_password("pw-1234567890")))
    db.commit()
    for _ in range(10):
        r = client.post("/login", data={
            "email": "a@x.com", "password": "wrong"})
        assert r.status_code == 401
    r = client.post("/login", data={
        "email": "a@x.com", "password": "pw-1234567890"})
    assert r.status_code == 401  # throttled even with the password


def test_csrf_required(client):
    make_user(client, "cs@x.com")
    r = client.post("/campaign/new", data={"name": "X"})
    assert r.status_code == 400


def test_tenant_isolation(client):
    make_user(client, "t1@x.com")
    token = csrf_of(client)
    client.post("/campaign/new", data={
        "name": "A's campaign", "csrf": token})
    from app.db import session
    from app.models import Campaign
    cid = session().query(Campaign).first().id
    client.post("/logout", data={"csrf": token})
    make_user(client, "t2@x.com")
    r = client.get(f"/campaign/{cid}")
    assert r.status_code == 404  # cross-tenant read denied
    r = client.post(f"/campaign/{cid}/generate",
                    data={"csrf": csrf_of(client)})
    assert r.status_code == 302  # redirects with error, no data


def test_consent_separation_and_withdrawal(client):
    make_user(client, "c@x.com")
    token = csrf_of(client)
    client.post("/campaign/new", data={
        "name": "Camp", "csrf": token})
    from app.db import session
    from app.models import Campaign
    cid = session().query(Campaign).filter_by(
        name="Camp").one().id
    r = client.post(f"/campaign/{cid}/metric", data={
        "csrf": token, "impressions": "10", "clicks": "2"})
    assert r.status_code == 403  # no analytics consent yet
    client.post("/consent", data={
        "csrf": token, "analytics": "on"})
    r = client.post(f"/campaign/{cid}/metric", data={
        "csrf": token, "impressions": "10", "clicks": "2"})
    assert r.status_code == 302
    from app.models import Metric
    assert session().query(Metric).count() == 1
    client.post("/consent", data={"csrf": token})  # withdraw
    assert session().query(Metric).count() == 0


def test_generation_template_labeled_and_safe(client):
    make_user(client, "g@x.com")
    token = csrf_of(client)
    client.post("/brand", data={
        "csrf": token, "display_name": "Nova",
        "voice": "witty", "audience": "adults 25-40",
        "intensity": "3", "restrictions": "no politics",
        "cta": "Subscribe (18+)"})
    client.post("/campaign/new", data={
        "name": "June push", "csrf": token})
    from app.db import session
    from app.models import Campaign, Variant
    cid = session().query(Campaign).filter_by(
        name="June push").one().id
    r = client.post(f"/campaign/{cid}/generate",
                    data={"csrf": token})
    assert r.status_code == 302
    vs = session().query(Variant).filter_by(
        campaign_id=cid).all()
    assert len(vs) == 2
    assert all(v.source == "template" for v in vs)
    c = json.loads(vs[0].content_json)
    assert "hook" in c and "caption" in c and "cta" in c


def test_output_validation_rejects_banned_and_promises():
    from app import marketing
    bad, errs = marketing.validate_output({
        "hook": "meet an underage star", "caption": "x",
        "cta": "y"})
    assert bad is None and any("banned" in e for e in errs)
    bad, errs = marketing.validate_output({
        "hook": "guaranteed income tonight", "caption": "x",
        "cta": "y"})
    assert bad is None and any("revenue" in e for e in errs)
    ok, errs = marketing.validate_output({
        "hook": "New content this week", "caption": "subscribe",
        "cta": "join"})
    assert ok is not None and not errs


def test_provider_failures_visible():
    from app import marketing
    with pytest.raises(marketing.GenerationError) as e:
        marketing._call_provider(
            {"AI_PROVIDER_URL": "http://127.0.0.1:9/x",
             "AI_PROVIDER_KEY": "k"}, {})
    assert e.value.status in ("provider_unavailable",
                              "provider_timeout")


def test_seo_ssrf_blocked():
    from app import seo
    for url in ("http://127.0.0.1/", "http://localhost/",
                "http://169.254.169.254/latest/meta-data/",
                "http://10.1.2.3/", "http://192.168.0.1/",
                "http://[::1]/", "file:///etc/passwd",
                "http://0.0.0.0/"):
        with pytest.raises(seo.FetchRefused):
            seo.assert_public_url(url)


def test_sitemap_generation():
    from app import seo
    xml = seo.generate_sitemap(
        ["https://example.com/", "https://example.com/x"])
    urls, problems = seo.validate_sitemap(xml)
    assert len(urls) == 2 and not problems
    with pytest.raises((seo.FetchRefused, ValueError)):
        seo.generate_sitemap(["http://127.0.0.1/"])


def test_webhook_boundary(client):
    import hashlib
    import hmac as h
    secret = "whsec_123"
    payload = json.dumps({
        "type": "invoice.paid", "amount": 999}).encode()
    ts = str(int(time.time()))
    sig = h.new(secret.encode(), payload,
                hashlib.sha256).hexdigest()
    h1 = {"X-Signature": "bad", "X-Timestamp": ts,
          "X-Provider": "prov", "X-Event-Id": "e1"}
    r = client.post("/webhook/payment", data=payload,
                    headers=h1)
    assert r.status_code == 400  # no secret configured: refused
    # configure secret via a second client is heavy; test the
    # service directly with a config dict
    from app import payments
    cfg = {"PAYMENT_WEBHOOK_SECRET": secret}
    payments.verify_webhook(cfg, "prov", "e2", ts, payload, sig)
    with pytest.raises(payments.WebhookRefused):
        payments.verify_webhook(cfg, "prov", "e3", ts, payload,
                                "bad")
    with pytest.raises(payments.WebhookRefused):
        payments.verify_webhook(cfg, "prov", "e4",
                                str(int(time.time()) - 9999),
                                payload, sig)
    with pytest.raises(payments.WebhookRefused):
        payments.verify_webhook(cfg, "prov", "e2", ts, payload,
                                sig)  # duplicate idempotent refusal


def test_monthly_pipeline_idempotent_and_honest(client):
    make_user(client, "m@x.com")
    token = csrf_of(client)
    client.post("/consent", data={
        "csrf": token, "analytics": "on"})
    client.post("/brand", data={
        "csrf": token, "display_name": "Nova",
        "voice": "witty", "audience": "adults",
        "intensity": "2"})
    for name, imps in (("A", 500), ("B", 500)):
        client.post("/campaign/new", data={
            "name": name, "csrf": token})
        from app.db import session
        from app.models import Campaign
        cid = session().query(Campaign).filter_by(
            name=name).one().id
        client.post(f"/campaign/{cid}/metric", data={
            "csrf": token, "impressions": imps,
            "clicks": imps // 20, "conversions": 2,
            "revenue_cents": 500, "spend_cents": 100,
            "source": "manual"})
    r = client.post("/review", data={
        "csrf": token, "period": "2026-10"})
    assert r.status_code == 302
    from app.db import session
    from app.models import MonthlyReview
    assert session().query(MonthlyReview).count() == 1
    client.post("/review", data={
        "csrf": token, "period": "2026-10"})  # idempotent rerun
    assert session().query(MonthlyReview).count() == 1
    row = session().query(MonthlyReview).one()
    assert row.status == "proposed"
    client.post(f"/review/{row.id}/decide", data={
        "csrf": token, "decision": "approved"})
    assert session().query(MonthlyReview).one().status \
        == "approved"
    # other tenant cannot decide on it
    client.post("/logout", data={"csrf": token})
    make_user(client, "m2@x.com")
    r = client.post(f"/review/{row.id}/decide", data={
        "csrf": csrf_of(client), "decision": "rejected"})
    assert r.status_code == 404


def test_wilson():
    from app.monthly import wilson
    lo, hi = wilson(60, 100)
    assert 0.5 < lo < 0.7 and lo < hi
    assert wilson(0, 0) == (0.0, 0.0)


def test_results_carry_intensity_and_channel(client):
    """Normalization data travels with the result row."""
    c = client
    make_user(c, "ric@x.com")
    tok = csrf_of(client)
    c.post("/brand", data={"csrf": tok, "display_name": "N",
                           "voice": "wry", "audience": "adults",
                           "intensity": "4", "channels": "x"})
    c.post("/consent", data={"csrf": tok, "analytics": "on"})
    c.post("/campaign/new", data={"csrf": tok, "name": "NR",
                                  "channel": "instagram"})
    from app.db import session
    from app.models import Campaign
    cid = session().query(Campaign).filter_by(
        name="NR").one().id
    c.post(f"/campaign/{cid}/metric",
           data={"csrf": tok, "impressions": "100"})
    from app.models import Metric
    m = session().query(Metric).order_by(
        Metric.id.desc()).first()
    assert m.intensity == 4 and m.channel == "instagram"


def test_feedback_stored_and_scoped(client):
    c = client
    make_user(c, "fb@x.com")
    tok = csrf_of(client)
    c.post("/brand", data={"csrf": tok, "display_name": "N",
                           "voice": "wry", "audience": "a",
                           "intensity": "2"})
    c.post("/campaign/new", data={"csrf": tok, "name": "FB",
                                  "channel": "instagram"})
    from app.db import session
    from app.models import Campaign
    cid = session().query(Campaign).filter_by(
        name="FB").one().id
    r = c.post(f"/campaign/{cid}/feedback",
               data={"csrf": tok, "body": "hook B felt stronger"})
    assert r.status_code == 302
    r = c.post(f"/campaign/{cid}/feedback",
               data={"csrf": tok, "body": "   "})
    assert r.status_code == 400  # empty feedback refused
    from app.models import CreatorFeedback
    fb = session().query(CreatorFeedback).all()
    assert len(fb) == 1 and "stronger" in fb[0].body


def test_lifecycle_release_requires_testing(client):
    c = client
    make_user(c, "lc@x.com")
    tok = csrf_of(client)
    """No path releases a change without a recorded experiment
    state; rollback is reachable from both testing and released."""
    c.post("/brand", data={"csrf": tok, "display_name": "N",
                           "voice": "wry", "audience": "a",
                           "intensity": "2"})
    c.post("/consent", data={"csrf": tok, "analytics": "on"})
    c.post("/campaign/new", data={"csrf": tok, "name": "LC",
                                  "channel": "instagram"})
    from app.db import session
    from app.models import Campaign
    cid = session().query(Campaign).filter_by(
        name="LC").one().id
    c.post(f"/campaign/{cid}/metric",
           data={"csrf": tok, "impressions": "150",
                 "clicks": "30", "conversions": "6"})
    c.post("/review", data={"csrf": tok, "period": "2025-01"})
    from app.models import MonthlyReview, User
    uid = session().query(User).filter_by(
        email="lc@x.com").one().id
    rid = session().query(MonthlyReview).filter_by(
        user_id=uid, period="2025-01").one().id
    # release without testing: refused
    r = c.post(f"/review/{rid}/lifecycle",
               data={"csrf": tok, "action": "release"})
    assert r.status_code == 403
    c.post(f"/review/{rid}/decide",
           data={"csrf": tok, "decision": "approved"})
    c.post(f"/review/{rid}/lifecycle",
            data={"csrf": tok, "action": "test"})
    c.post(f"/review/{rid}/lifecycle",
            data={"csrf": tok, "action": "release"})
    row = session().query(MonthlyReview).filter_by(
        id=rid).one()
    assert row.status == "released"
    import json as _json
    log = _json.loads(row.transition_log)
    assert [e["to"] for e in log] == ["testing", "released"]
    c.post(f"/review/{rid}/lifecycle",
           data={"csrf": tok,
                 "action": "rollback_after_release"})
    assert session().query(MonthlyReview).filter_by(
        id=rid).one().status == "rolled_back"


def test_checkout_link_out_validation(client):
    c = client
    make_user(c, "co@x.com")
    tok = csrf_of(client)
    """Creator content sales are a link-out, https-only."""
    r = c.post("/brand", data={"csrf": tok, "display_name": "N",
                               "voice": "wry", "audience": "a",
                               "intensity": "2",
                               "checkout_url": "http://bad.example"})
    assert r.status_code == 400
    r = c.post("/brand", data={"csrf": tok, "display_name": "N",
                               "voice": "wry", "audience": "a",
                               "intensity": "2",
                               "checkout_url":
                               "https://pay.example/me"})
    assert r.status_code == 302
    from app.db import session
    from app.models import BrandProfile, User
    uid = session().query(User).filter_by(
        email="co@x.com").one().id
    bp = session().query(BrandProfile).filter_by(
        user_id=uid).one()
    assert bp.checkout_url == "https://pay.example/me"


def test_payment_flows_separate_and_fail_closed(client):
    """Three flows, structurally separate providers, verified
    payments only. No configured provider means no checkout, never
    a fake one."""
    from app import payments as P
    # unconfigured: every flow refuses to create a checkout
    for flow in P.FLOWS:
        a = P.adapter_for(flow)
        try:
            a.create_checkout("t1", 100, {})
            raise SystemExit(f"{flow} leaked an unconfigured checkout")
        except P.AdapterError:
            pass
    # policy: stripe may NOT take the creator-checkout flow,
    # even WITH a secret configured
    try:
        P.configure_provider(P.CREATOR_CHECKOUT, "stripe",
                            {"PAYMENT_STRIPE_SECRET": "x"})
        raise SystemExit("stripe accepted adult checkout flow")
    except P.AdapterError:
        pass
    # cash_app is permitted for NO flow, secret or not
    try:
        P.configure_provider(P.SAAS_SUBSCRIPTION, "cash_app",
                            {"PAYMENT_CASH_APP_SECRET": "x"})
        raise SystemExit("cash_app accepted a flow")
    except P.AdapterError:
        pass
    # segpay may take the creator-checkout flow when configured
    # (undeclared category defaults to ADULT, the strictest)
    ad = P.configure_provider(
        P.CREATOR_CHECKOUT, "segpay",
        {"PAYMENT_SEGPAY_SECRET": "k"})
    assert ad.provider == "segpay"
    # category-neutral platform: a GENERAL creator (business,
    # art, courses, anything unrestricted) may use standard
    # processors; an ADULT creator may not
    ad = P.configure_provider(
        P.CREATOR_CHECKOUT, "stripe",
        {"PAYMENT_STRIPE_SECRET": "k"},
        category=P.CREATOR_CATEGORY_GENERAL)
    assert ad.provider == "stripe"
    ad = P.configure_provider(
        P.CREATOR_CHECKOUT, "cash_app",
        {"PAYMENT_CASH_APP_SECRET": "k"},
        category=P.CREATOR_CATEGORY_GENERAL)
    assert ad.provider == "cash_app"
    # unknown category is refused, not guessed
    try:
        P.configure_provider(
            P.CREATOR_CHECKOUT, "segpay",
            {"PAYMENT_SEGPAY_SECRET": "k"}, category="unknown")
        raise SystemExit("unknown category accepted")
    except P.AdapterError:
        pass
    # unlock gate: only a server-verified payment record opens it
    make_user(client, "pay@x.com")
    from app.db import session
    from app.models import User
    uid = session().query(User).filter_by(
        email="pay@x.com").one().id
    assert not P.payment_is_verified(
        P.CREATOR_CHECKOUT, uid, "ref-1")
    P.record_verified_payment(P.CREATOR_CHECKOUT, uid,
                              "ref-1", "segpay")
    assert P.payment_is_verified(
        P.CREATOR_CHECKOUT, uid, "ref-1")
    # idempotent: the same reference does not duplicate
    P.record_verified_payment(P.CREATOR_CHECKOUT, uid,
                              "ref-1", "segpay")
    from app.models import VerifiedPayment
    n = session().query(VerifiedPayment).filter_by(
        tenant_id=uid, reference="ref-1").count()
    assert n == 1
    # tenant separation: another tenant cannot see the payment
    assert not P.payment_is_verified(
        P.CREATOR_CHECKOUT, "other-tenant", "ref-1")


def test_share_keywords_honest(client):
    make_user(client, "kw@example.com")
    token = csrf_of(client)
    # empty profile -> no keywords, never filler
    r = client.get("/share")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "nothing invented" in body
    assert "github.com/1243353366/creator-marketing-platform" in body
    # fill the brand profile; keywords must come from it
    r = client.post("/brand", data={
        "csrf": token, "display_name": "Sunny Ceramics Studio",
        "voice": "warm", "audience": "home decor lovers",
        "intensity": 2, "cta": "shop handmade mugs",
        "business_url": "https://maps.example/sunny-ceramics"})
    assert r.status_code == 302, r.status_code
    body = client.get("/share").get_data(as_text=True)
    for kw in ("sunny", "ceramics", "studio", "handmade", "mugs"):
        assert kw in body.lower(), kw
    # no invented content: stopword filler absent from the list
    assert "mugs, the" not in body.lower()
    # place of business renders
    assert "https://maps.example/sunny-ceramics" in body


def test_share_business_url_validation(client):
    make_user(client, "kw2@example.com")
    token = csrf_of(client)
    r = client.post("/brand", data={
        "csrf": token, "display_name": "x", "voice": "y",
        "audience": "z", "intensity": 2,
        "business_url": "http://insecure.example/x"})
    assert r.status_code == 400
    assert b"Business link must be https://" in r.data


def test_share_link_formats():
    from app.share import (meta_share_url, generate_keywords,
                           REFERRAL_URL)
    # adversarial url cannot inject sharer parameters
    u = meta_share_url("https://x.example/a?b=1&c=2#f")
    assert u.startswith(
        "https://www.facebook.com/sharer/sharer.php?u=")
    assert "%26" in u and "%3F" in u and "#f" not in u
    # deterministic, capped, own-data only
    class P:
        display_name = "Alpha Beta Gamma"
        voice = "alpha voice test"
        audience = ""
        cta = ""
        channels = ""
        conversion_goal = ""
    kws = generate_keywords(P(), "")
    assert kws[0] == "alpha" and "beta" in kws
    assert len(kws) <= 12
    assert "the" not in kws and "and" not in kws
    assert REFERRAL_URL.endswith("creator-marketing-platform")
