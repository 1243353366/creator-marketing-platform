"""Error-path completeness audit: trigger every user-facing
validation failure and verify the response

  1. REJECTS the input (4xx, or the change never happened),
  2. EXPLAINS the problem in human-readable text, and
  3. LEAKS NO internals - no tracebacks, driver names, SQL,
     filesystem paths, or exception class names.

A bare abort(400) with no message fails this audit: a user who
is told nothing was told nothing. Exit 1 on any failure.
Bootstraps its own throwaway database, like the oracle probe.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PGINSTALL", "/app/pginstall")

tmp = tempfile.mkdtemp(prefix="cmp-erraudit-")
from pgserver import PostgresServer  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from app.db import Base  # noqa: E402
import app.models  # noqa: E402,F401
from app import create_app  # noqa: E402

pgroot = Path(tmp) / "pg"
pgroot.mkdir(parents=True)   # pgserver asserts the dir exists
os.environ["DATABASE_URL"] = PostgresServer(
    pgroot).get_uri()
eng = create_engine(os.environ["DATABASE_URL"])
Base.metadata.create_all(eng)
eng.dispose()

app = create_app(config={
    "DATABASE_URL": os.environ["DATABASE_URL"],
    "SECRET_KEY": "erraudit" + "k" * 24, "TESTING": True})

# internals that must never reach a response body
LEAKS = re.compile(
    r"Traceback|sqlalchemy|psycopg|select |insert into|"
    r"/app/|<class |Exception|SQLSTATE|errno")
cases = []   # (name, status_spec, response, must_contain)


def case(name, r, expect, *needles):
    cases.append((name, r.status_code, expect, r, needles))


def auth_user_id(client):
    with client.session_transaction() as s:
        tok = s.get("token", "")
    from app import auth
    u, _ = auth.user_for_token(tok)
    return u.id


def user(client, email, name="Sunny Studio", audience="home decor"):
    client.post("/signup", data={
        "email": email, "password": "password-12345"})
    with client.session_transaction() as s:
        tok = s.get("token", "")
    from app import auth
    csrf = auth.user_for_token(tok)[1].csrf_token
    client.post("/brand", data={
        "csrf": csrf, "display_name": name, "voice": "warm",
        "audience": audience, "intensity": 2})
    return csrf


c = app.test_client()

# --- auth ------------------------------------------------
r = c.post("/signup", data={"email": "not-an-email",
                            "password": "password-12345"})
case("signup bad email", r, 400, "email")
r = c.post("/signup", data={"email": "ok@example.com",
                            "password": "short"})
case("signup weak password", r, 400, "password")
c.post("/signup", data={"email": "dupe@example.com",
                        "password": "password-12345"})
r = c.post("/signup", data={"email": "dupe@example.com",
                            "password": "password-12345"})
case("signup duplicate email", r, (200, 400, 409), "registered")
r = c.post("/login", data={"email": "dupe@example.com",
                           "password": "wrong-password"})
case("login wrong password", r, (200, 400, 401), "credential",
     "password", "Invalid", "invalid")
r = c.post("/campaign/new", data={"name": "x"})  # logged in
case("post without csrf", r, (400, 403), "CSRF")
anon = app.test_client()
r = anon.get("/dashboard")
case("anonymous authed page", r, (301, 302, 401), "login",
     "Log in", "Sign up")

# --- brand form ------------------------------------------
csrf = user(c, "brand@example.com")
r = c.post("/brand", data={
    "csrf": csrf, "display_name": "x", "voice": "y",
    "audience": "z", "intensity": 2,
    "checkout_url": "http://plain.example/x"})
case("checkout non-https", r, 400, "https")
r = c.post("/brand", data={
    "csrf": csrf, "display_name": "x", "voice": "y",
    "audience": "z", "intensity": 2,
    "business_url": "http://plain.example/y"})
case("business non-https", r, 400, "https")

# --- campaign + review -----------------------------------
r = c.post("/campaign/new", data={"csrf": csrf, "name": ""})
case("campaign empty name", r, (400, 422), "name")
r = c.post("/review", data={"csrf": csrf, "period": "junk"})
case("review bad period", r, 400, "YYYY-MM")

# --- seo -------------------------------------------------
r = c.post("/seo", data={"csrf": csrf,
                         "url": "http://10.255.0.7/x"})
case("seo private address", r, (400, 200), "refus", "Refused",
     "private", "not public", "banned", "cannot")

# --- consent gate + feedback (real routes) ---------------
c.post("/campaign/new", data={"csrf": csrf, "name": "Err audit"})
from app.models import Campaign
from app.db import session as dbs
cid = dbs().query(Campaign).filter_by(
    user_id=auth_user_id(c)).first().id
r = c.post(f"/campaign/{cid}/metric", data={
    "csrf": csrf, "kind": "ctr", "value": "0.1"})
case("metrics without consent", r, (400, 403),
     "consent", "Consent")
r = c.post(f"/campaign/{cid}/feedback", data={
    "csrf": csrf, "body": ""})
case("feedback empty", r, (400, 403), "empty", "feedback")

# --- rate limit ------------------------------------------
c3 = app.test_client()
for _ in range(10):   # auth.MAX_FAILURES is 10
    c3.post("/login", data={"email": "nobody@example.com",
                            "password": "bad-bad-bad"})
r = c3.post("/login", data={"email": "nobody@example.com",
                            "password": "bad-bad-bad"})
case("login rate limit", r, (429, 200, 400, 401),
     "rate", "limit", "later", "slow", "many", "too many")

failures = []
for name, status, expect, r, needles in cases:
    body = r.get_data(as_text=True)
    bad = []
    if isinstance(expect, tuple):
        if status not in expect:
            bad.append(f"status {status} not in {expect}")
    elif status != expect:
        bad.append(f"status {status} != {expect}")
    if not any(n.lower() in body.lower() for n in needles):
        bad.append("no human explanation found "
                   f"(looked for {needles})")
    leak = LEAKS.search(body)
    if leak:
        bad.append(f"internal leak: {leak.group()!r}")
    if bad:
        failures.append(f"{name}: " + "; ".join(bad))

print(f"error-path audit: {len(cases)} cases, "
      f"{len(failures)} failures")
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
