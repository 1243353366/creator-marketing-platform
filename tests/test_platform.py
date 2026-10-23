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
