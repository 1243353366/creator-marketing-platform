"""The ten first tests of the Oracle / Pilot / Plane core
failure model. Each test names its domain and cross-system
class, per the model, and proves a control - not a claim.

  Oracle  = decision failures (wrong evidence, false approval)
  Pilot   = execution failures (wrong target, silent error)
  Plane   = infrastructure failures (env, isolation, recovery)
"""
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_platform import make_user, csrf_of  # noqa: E402


# --- 1. Plane: wrong environment must fail closed -------------
def test_plane_wrong_environment_refused():
    """Plane / fail-open outage. Booting against a missing or
    weak SECRET_KEY must REFUSE, never serve fail-open."""
    from app import create_app
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(config={"DATABASE_URL":
                           "postgresql://x", "SECRET_KEY": ""})
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(config={"DATABASE_URL": "postgresql://x",
                           "SECRET_KEY": "short"})  # < 16 chars


# --- 2. Plane/cross-system: unauthorized cross-tenant ---------
def test_cross_tenant_unauthorized_access(client):
    """Plane+Pilot / cross-tenant leakage. B touching A's
    campaign by id must get 404 - never A's data."""
    make_user(client, "fmA@example.com")
    tok_a = csrf_of(client)
    client.post("/campaign/new", data={
        "csrf": tok_a, "name": "Fm camp A"})
    from app.db import session as dbs
    from app.models import Campaign
    cid = dbs().query(Campaign).filter_by(
        name="Fm camp A").first().id
    make_user(client, "fmB@example.com")
    r = client.get(f"/campaign/{cid}")
    assert r.status_code == 404
    assert b"Fm camp A" not in r.data
    r = client.post(f"/campaign/{cid}/generate",
                    data={"csrf": csrf_of(client)})
    assert r.status_code == 404


# --- 3. Oracle: oracle failure must not become approval ------
def test_oracle_failure_is_not_approval(client):
    """Oracle / circular-verification + false approval. A
    forged payment signature must leave NO verified payment
    and NO unlock - refusal must not look like success."""
    make_user(client, "or@example.com")
    payload = json.dumps({
        "type": "invoice.paid", "amount": 999}).encode()
    ts = str(int(time.time()))
    forged = {"X-Signature": "deadbeef",
              "X-Timestamp": ts, "X-Provider": "prov",
              "X-Event-Id": "evil-1"}
    client.application.config[
        "PAYMENT_WEBHOOK_SECRET"] = "whsec_real"
    r = client.post("/webhook/payment", data=payload,
                    headers=forged)
    assert r.status_code == 400
    from app.models import VerifiedPayment
    assert dbs_count(VerifiedPayment) == 0, (
        "forged payment produced a verified record")


def dbs_count(model):
    from app.db import session as dbs
    return dbs().query(model).count()


# --- 4. Pilot: validation errors visible in the UI ------------
def test_validation_errors_reach_the_user(client):
    """Pilot / silent failure. A rejected input must come
    back with a human explanation the template renders."""
    make_user(client, "ui@example.com")
    tok = csrf_of(client)
    r = client.post("/brand", data={
        "csrf": tok, "display_name": "x", "voice": "y",
        "audience": "z", "intensity": 2,
        "checkout_url": "http://not-https.example/"})
    assert r.status_code == 400
    assert b"must be https://" in r.data, (
        "400 rendered without the reason")


# --- 5. Pilot: failed operation never reported success -------
def test_failed_operation_not_reported_success(client):
    """Pilot / false success. Adding an unreachable page must
    be refused AND leave no page row - no success theater."""
    make_user(client, "fp@example.com")
    tok = csrf_of(client)
    r = client.post("/seo", data={
        "csrf": tok, "url": "https://no-such-host-xyz.invalid/"})
    from app.models import SeoPage
    if r.status_code == 200:
        assert b"Refused" in r.data or b"refus" in r.data
    else:
        assert r.status_code == 400
    assert dbs_count(SeoPage) == 0, (
        "refused page add still created a row")


# --- 6. Pilot/cross-system: replay must not duplicate ---------
def test_replay_creates_no_duplicate_side_effects(client):
    """Pilot / unsafe retry. The SAME valid webhook delivered
    twice (network retry) must verify once and only once."""
    make_user(client, "rp@example.com")
    secret = "whsec_2"
    payload = json.dumps({
        "type": "invoice.paid", "amount": 500}).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), payload,
                   hashlib.sha256).hexdigest()
    client.application.config[
        "PAYMENT_WEBHOOK_SECRET"] = secret
    h = {"X-Signature": sig, "X-Timestamp": ts,
         "X-Provider": "prov", "X-Event-Id": "evt-1"}
    r1 = client.post("/webhook/payment", data=payload,
                     headers=h)
    assert r1.status_code == 200          # first delivery verifies
    r2 = client.post("/webhook/payment", data=payload,
                     headers=h)           # network retry: refused
    assert r2.status_code == 400
    from app.models import WebhookEvent
    assert dbs_count(WebhookEvent) == 1, (
        "replayed webhook left duplicate event rows")
    # and the recording layer is retry-safe by design
    from app import payments
    from app.db import session as dbs
    from app.models import User
    uid = dbs().query(User).first().id
    payments.record_verified_payment(
        "saas_subscription", uid, "sub-1", "prov")
    payments.record_verified_payment(       # duplicate call = retry
        "saas_subscription", uid, "sub-1", "prov")
    from app.models import VerifiedPayment
    assert dbs_count(VerifiedPayment) == 1, (
        "retried record_verified_payment duplicated the row")


# --- 7. Plane/cross-system: no secrets in responses -----------
def test_no_sensitive_data_in_responses(client):
    """Plane / evidence corruption + data leak. No page or
    error body may contain the submitted password, an argon2
    hash, or a session token."""
    make_user(client, "sec@example.com", password="pw-12345-xyz")
    csrf_of(client)
    with client.session_transaction() as s:
        session_token = s["token"]       # the real session secret
    assert b"pw-12345-xyz" not in client.get(
        "/dashboard").data
    for path in ("/brand", "/seo", "/share", "/payments",
                 "/consent", "/review", "/seo/sitemap",
                 "/seo/audits"):
        body = client.get(path).data
        assert b"pw-12345-xyz" not in body, path
        assert b"$argon2" not in body, path
        assert session_token.encode() not in body, path
    r = client.post("/login", data={
        "email": "sec@example.com",
        "password": "wrong-password-9"})
    assert b"pw-12345-xyz" not in r.data


# --- 8. cross-system: skipped CI checks cannot read as green --
def test_ci_gate_integrity():
    """cross-system / false-green release + evidence
    corruption. Every sentinel must be present in ci.yml AND
    exist on disk with content - a deleted or emptied guard
    must fail here, not silently pass."""
    ci = Path(".github/workflows/ci.yml").read_text()
    required = [
        "ruff check",                 # lint
        "syntax_gate.py",             # static integrity
        "error_paths_audit.py",        # error-path completeness
        "check_schema_parity.py",     # model/migration drift
        "oracle_probe.py",            # wrong-target sentinel
        "-m pytest",                  # regression suite
    ]
    for needle in required:
        assert needle in ci, (
            f"CI no longer runs the sentinel: {needle}")
    for script in ("scripts/syntax_gate.py",
                   "scripts/error_paths_audit.py",
                   "scripts/check_schema_parity.py",
                   "scripts/oracle_probe.py"):
        p = Path(script)
        assert p.is_file() and p.stat().st_size > 500, (
            f"sentinel missing or emptied: {script}")


# --- 9. Plane: rollback must leave consistent state ----------
def test_rollback_recovery_consistency():
    """Plane / corrupted recovery. Downgrade then re-upgrade
    must end at a schema identical to the models - recovery
    that drifts is worse than no recovery."""
    from alembic import command
    from alembic.config import Config
    from pgserver import PostgresServer
    from sqlalchemy import create_engine, inspect

    tmp = tempfile.mkdtemp(prefix="cmp-rollback-")
    pgroot = Path(tmp) / "pg"
    pgroot.mkdir(parents=True)
    os.environ["DATABASE_URL"] = PostgresServer(
        pgroot).get_uri()

    def schema(engine):
        insp = inspect(engine)
        return {t: sorted(c["name"] for c in
                          insp.get_columns(t))
                for t in insp.get_table_names()
                if t != "alembic_version"}

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    engine = create_engine(os.environ["DATABASE_URL"])
    full = schema(engine)
    command.downgrade(cfg, "-1")
    rolled = schema(engine)
    assert rolled != full, "downgrade changed nothing?"
    command.upgrade(cfg, "head")
    again = schema(engine)
    engine.dispose()
    assert again == full, (
        "re-upgrade drifted from the original head schema")
    # and head must match the models the app actually uses
    from app.db import Base
    expected = {t.name: sorted(c.name for c in t.columns)
                for t in Base.metadata.sorted_tables}
    assert expected == again, (
        "migrated schema disagrees with app models")


# --- 10. cross-system: sentinel cannot be fooled -------------
def test_sentinel_catches_its_own_target_class(tmp_path):
    """cross-system / sentinel bypassed. A sentinel that cannot
    find its target must FAIL LOUDLY, not pass vacuously -
    the self-match drill, run against the syntax gate."""
    root = Path.cwd()
    copy = tmp_path / "repo"
    shutil.copytree(root, copy,
                    ignore=shutil.ignore_patterns(
                        ".git", "__pycache__", "*.pyc",
                        ".pytest_cache"))
    gate = copy / "scripts" / "syntax_gate.py"
    # case 1: a genuinely broken template must be caught
    (copy / "app/templates/broken.html").write_text(
        "{% if unclosed ")
    r = subprocess.run([sys.executable, str(gate)],
                       capture_output=True, text=True,
                       cwd=copy, timeout=120)
    assert r.returncode != 0, (
        "syntax gate passed a broken template")
    assert "broken.html" in r.stdout
    # case 2: damage fingerprint must be caught
    (copy / "app/templates/broken.html").write_text("ok")
    (copy / "app/broken_marker.py").write_text(
        "# TO" + "DO fix later\nx = 1\n")
    r = subprocess.run([sys.executable, str(gate)],
                       capture_output=True, text=True,
                       cwd=copy, timeout=120)
    assert r.returncode != 0, (
        "syntax gate passed a damage marker")
    assert "broken_marker" in r.stdout
