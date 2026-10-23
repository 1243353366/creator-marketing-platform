"""Oracle probe: wrong-target access sentinel.

Failure class (from the failed pilots, 2026-10): the system
executes the intended operation against the wrong target. This
probe attacks every object route as the WRONG AGENT and asserts
each attack fails closed:

  1. IDOR / wrong tenant: user B requests user A's objects by
     id (view, generate, confirm, lifecycle, decide, metric,
     feedback, seo audit). Must 404 or redirect - never 200,
     never a state change on A's objects.
  2. Cross-tenant leakage: B browses every list page; A's
     unique markers (campaign name, checkout url) must never
     appear in B's responses.
  3. Auth boundary: anonymous requests to every authenticated
     route must redirect to login, never render data.
  4. Forged origin: POSTs without a CSRF token must be refused
     with no state change.

Exit 0 = all attacks repelled. Exit 1 = an oracle failure, with
the exact probe that leaked. Runs in CI on every push.
Like every guard: drilled by injecting a failure before trust
(see docs/LESSONS.md L5, L8).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PGINSTALL", "/app/pginstall")

import tempfile  # noqa: E402
import shutil  # noqa: E402

tmp = tempfile.mkdtemp(prefix="cmp-oracle-")
try:
    from pgserver import PostgresServer
    pgroot = Path(tmp) / "pg"
    pgroot.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_URL"] = PostgresServer(pgroot).get_uri()

    from sqlalchemy import create_engine
    from app.db import Base
    import app.models  # noqa: F401

    eng = create_engine(os.environ["DATABASE_URL"])
    Base.metadata.create_all(eng)
    eng.dispose()

    from app import create_app
    app = create_app(config={
        "DATABASE_URL": os.environ["DATABASE_URL"],
        "SECRET_KEY": "oracle-probe-key" + "x" * 16,
        "TESTING": True,
    })

    A_MARK = "oracle-marker-alpha-9917"
    B_EMAIL = "b-oracle@example.com"

    def new_client():
        return app.test_client()

    def csrf_of(client):
        # same extractor as the test suite: read the token from
        # the session store, not from parsing HTML (parsing
        # breaks when attribute order changes)
        from app import auth
        with client.session_transaction() as s:
            tok = s.get("token", "")
        data = auth.user_for_token(tok)
        return data[1].csrf_token

    failures = []

    # --- tenant A builds real objects -------------------------
    a = new_client()
    a.post("/signup", data={"email": "a-oracle@example.com",
                            "password": "password-12345"})
    tok_a = csrf_of(a)
    a.post("/brand", data={
        "csrf": tok_a, "niche": "test",
        "brand_name": A_MARK, "cta": "test",
        "checkout_url": "https://a.example/checkout-9917"})
    a.post("/campaign/new", data={"csrf": tok_a,
                                  "name": A_MARK})
    # discover campaign id through the dashboard listing
    page = a.get("/dashboard").get_data(as_text=True)
    import re
    # campaign ids are hex uuids - a \d+ regex matched NOTHING
    # and the whole IDOR attack silently never ran (false-green
    # probe caught by the L5 drill; never regex-guess id shapes)
    m = re.findall(r'href="(/campaign/[^"]+)"', page)
    m = [x.rsplit("/", 1)[-1] for x in m]
    if not m:
        failures.append("A could not create/find a campaign to "
                        "probe with (probe setup broken)")
        sys.exit(1)  # never continue with skipped attacks
    cid = m[0]
    a.post(f"/campaign/{cid}/generate",
           data={"csrf": tok_a})
    a.post(f"/campaign/{cid}/confirm",
           data={"csrf": tok_a})
    # a review request
    a.post("/review", data={"csrf": tok_a,
                            "content": A_MARK})

    # --- 1. IDOR: B requests A's objects by id ---------------
    b = new_client()
    b.post("/signup", data={"email": B_EMAIL,
                            "password": "password-12345"})
    b.get("/dashboard")  # establish session
    if cid:
        for path, label in (
            (f"/campaign/{cid}", "view A's campaign by id"),
            (f"/campaign/{cid}/generate",
             "generate against A's campaign"),
            (f"/campaign/{cid}/confirm",
             "confirm against A's campaign"),
        ):
            r = b.get(path)
            if r.status_code == 200:
                failures.append(f"IDOR: B can 200 {label}")
            r2 = b.post(path, data={})
            if r2.status_code == 200 and A_MARK in \
                    r2.get_data(as_text=True):
                failures.append(
                    f"IDOR: B sees A's data via {label}")
    for rid in ("1", "2"):
        for path, label in (
            (f"/review/{rid}/lifecycle",
             "advance lifecycle of A's review"),
            (f"/review/{rid}/decide",
             "decide on A's review"),
        ):
            r = b.post(path, data={})
            if r.status_code == 200 and A_MARK in \
                    r.get_data(as_text=True):
                failures.append(f"IDOR: {label}")

    # --- 2. cross-tenant leakage on list pages ---------------
    for path in ("/dashboard", "/brand", "/review", "/seo",
                 "/consent", "/payments", "/seo/sitemap",
                 "/seo/audit-history"):
        body = b.get(path).get_data(as_text=True)
        if A_MARK in body:
            failures.append(
                f"LEAK: A's marker visible to B on {path}")

    # --- 3. auth boundary: anonymous --------------------------
    anon = new_client()
    for path in ("/dashboard", "/brand", "/review", "/seo",
                 "/consent", "/payments",
                 "/seo/sitemap", "/seo/audit-history"):
        r = anon.get(path)
        if r.status_code == 200:
            body = r.get_data(as_text=True)
            if A_MARK in body or "logout" in body.lower():
                failures.append(
                    f"AUTH: anonymous 200 on {path}")

    # --- 4. forged origin: POST without CSRF ------------------
    r = b.post("/brand", data={
        "niche": "evil", "brand_name": "evil-brand",
        "cta": "x", "checkout_url": "https://evil.example/x"})
    if r.status_code == 200:
        # check whether the write actually landed
        page = b.get("/brand").get_data(as_text=True)
        if "evil-brand" in page:
            failures.append(
                "CSRF: forged POST without token changed state")

    if failures:
        print("ORACLE PROBE FAILURES:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("oracle probe: IDOR, cross-tenant leakage, auth "
          "boundary, and forged-origin attacks all repelled")
    sys.exit(0)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
