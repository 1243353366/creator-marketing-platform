"""Tests for the bridge to the live Cloudflare Workers edition.

The bridge preserves the platform's honest-provider rules:
- AI output stays labeled with its real source end to end
- a configured bridge that fails raises - never a silent
  template swap
- with no bridge configured, behavior is exactly as before
"""
import json
import urllib.error
import urllib.request

import pytest

from tests.test_platform import make_user, csrf_of  # noqa: F401


def _bridge_cfg():
    return {"WORKERS_BRIDGE_URL": "https://worker.example"}


def _remote(content="Summer shoot teaser.\nSubscribe for the full set.",
            source="workers-ai", labeled=True):
    return {"content": content, "source": source, "labeled": labeled}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self, n):
        return json.dumps(self.payload).encode()[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_bridge_disabled_returns_none():
    from app import workers_bridge
    assert workers_bridge.bridge_generate(
        {}, {"objective": "o", "audience": "a",
             "voice": "v"}, 1) is None


def test_bridge_maps_variant_and_preserves_labels(monkeypatch):
    from app import workers_bridge
    seen = {}

    def fake_request(url, tenant_id, path, body):
        seen.update(url=url, tenant=tenant_id,
                    path=path, body=body)
        return _remote()

    monkeypatch.setattr(workers_bridge, "_request", fake_request)
    payload = {"objective": "new course", "audience": "adults",
               "voice": "witty", "cta": "Subscribe (18+)"}
    out = workers_bridge.bridge_generate(
        _bridge_cfg(), payload, 42)
    # honest labeling preserved end to end
    assert out["source"] == "bridge-ai"
    assert out["remote_source"] == "workers-ai"
    assert out["labeled"] is True
    # variant shape for validate_output
    assert out["hook"] == "Summer shoot teaser."
    assert out["caption"] == "Subscribe for the full set."
    assert out["cta"] == "Subscribe (18+)"
    # tenant header is the platform user, request shape correct
    assert seen["tenant"] == "platform-42"
    assert seen["path"] == "/api/generate"
    assert seen["body"]["platform"] == "blog"
    assert "new course" in seen["body"]["topic"]


def test_bridge_failure_statuses_never_silent(monkeypatch):
    from app import workers_bridge

    def raise_(e):
        def fake_request(*a, **k):
            raise e
        return fake_request

    cases = [
        (urllib.error.HTTPError(
            "u", 402, "Payment Required", {}, None), "over_budget"),
        (urllib.error.HTTPError(
            "u", 422, "Unprocessable", {}, None), "rejected"),
        (urllib.error.HTTPError(
            "u", 500, "Boom", {}, None), "provider_unavailable"),
        (urllib.error.URLError("no route"), "provider_unavailable"),
        (TimeoutError(), "provider_timeout"),
        (ValueError("bad json"), "malformed_output"),
    ]
    for err, status in cases:
        monkeypatch.setattr(
            workers_bridge, "_request", raise_(err))
        with pytest.raises(workers_bridge.BridgeError) as exc:
            workers_bridge.bridge_generate(
                _bridge_cfg(), {"objective": "o",
                                "audience": "a", "voice": "v"}, 1)
        assert exc.value.status == status, (
            f"{type(err).__name__} -> {exc.value.status}, "
            f"expected {status}")
    # empty content is malformed, not success
    monkeypatch.setattr(
        workers_bridge, "_request",
        lambda *a, **k: _remote(content="  \n"))
    with pytest.raises(workers_bridge.BridgeError) as exc:
        workers_bridge.bridge_generate(
            _bridge_cfg(), {"objective": "o", "audience": "a",
                            "voice": "v"}, 1)
    assert exc.value.status == "malformed_output"


def test_generate_via_bridge_labels_and_logs(client, monkeypatch):
    """End to end: with the bridge configured, generation uses
    the live Worker, labels variants bridge-ai, and logs ok."""
    from app import workers_bridge
    make_user(client, "br@x.com")
    token = csrf_of(client)
    client.post("/brand", data={
        "csrf": token, "display_name": "Nova",
        "voice": "witty", "audience": "adults 25-40",
        "intensity": "3", "restrictions": "no politics",
        "cta": "Subscribe (18+)"})
    client.post("/campaign/new", data={
        "name": "Bridge push", "csrf": token})
    from app.db import session
    from app.models import Campaign, Variant, GenerationLog
    cid = session().query(Campaign).filter_by(
        name="Bridge push").one().id
    client.application.config[
        "WORKERS_BRIDGE_URL"] = "https://worker.example"
    monkeypatch.setattr(
        workers_bridge, "_request",
        lambda *a, **k: _remote())
    r = client.post(f"/campaign/{cid}/generate",
                    data={"csrf": token})
    assert r.status_code == 302
    vs = session().query(Variant).filter_by(
        campaign_id=cid).all()
    assert len(vs) == 2
    assert all(v.source == "bridge-ai" for v in vs)
    logs = session().query(GenerationLog).filter_by(
        campaign_id=cid).all()
    assert logs and logs[-1].provider == "bridge-ai"
    assert logs[-1].status == "ok"


def test_generate_bridge_failure_raises_not_template(
        client, monkeypatch):
    """The core honesty rule: a configured bridge that is down
    fails loudly - the template must NOT silently masquerade as
    bridge output."""
    from app import workers_bridge
    make_user(client, "brd@x.com")
    token = csrf_of(client)
    client.post("/brand", data={
        "csrf": token, "display_name": "Nova",
        "voice": "witty", "audience": "adults 25-40",
        "intensity": "3", "restrictions": "none",
        "cta": "Subscribe (18+)"})
    client.post("/campaign/new", data={
        "name": "Bridge down", "csrf": token})
    from app.db import session
    from app.models import Campaign, Variant
    cid = session().query(Campaign).filter_by(
        name="Bridge down").one().id
    client.application.config[
        "WORKERS_BRIDGE_URL"] = "https://worker.example"

    def down(*a, **k):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(workers_bridge, "_request", down)
    r = client.post(f"/campaign/{cid}/generate",
                    data={"csrf": token})
    # the generate route redirects with an error param on
    # GenerationError; the explanation must name the bridge
    assert r.status_code == 302
    assert "bridge" in r.headers["Location"]
    assert session().query(Variant).filter_by(
        campaign_id=cid).count() == 0
