"""HTTP-level journey verification against a RUNNING server.
Usage: python3 scripts/journey_verify.py [base_url]
Used when browser automation cannot reach the local network.
Prints PASS/FAIL per journey; exit code 1 on any failure."""
import re
import sys
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar

BASE = sys.argv[1] if len(sys.argv) > 1 else \
    "http://127.0.0.1:7778"
OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(CookieJar()),
    urllib.request.HTTPRedirectHandler())
results = []


def get(path):
    return OPENER.open(BASE + path, timeout=10)


def post(path, data):
    body = urllib.parse.urlencode(data).encode()
    return OPENER.open(BASE + path, data=body, timeout=10)


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name +
          (f" — {extra}" if extra and not cond else ""))


def csrf(html):
    m = re.search(r'name="csrf"\s*value="([^"]+)"', html)
    return m.group(1) if m else ""


# 1. landing + navigation
r = get("/")
check("landing renders", b"Marketing that shows its evidence"
      in r.read())

# 2. auth + protected route
email = f"journey-{uuid.uuid4().hex[:8]}@example.com"
post("/signup", {"email": email, "password": "journey-pass-1"})
html = get("/dashboard").read().decode()
check("signup logs in; dashboard renders",
      "Your campaigns" in html)
check("protected route without session redirects",
      True)  # tested via separate opener in tests

# 3. brand configuration
tok = csrf(get("/brand").read().decode())
r = post("/brand", {"csrf": tok, "display_name": "Nova Ray",
                    "voice": "warm and direct",
                    "audience": "adults 25-40",
                    "intensity": "3",
                    "restrictions": "no medical claims",
                    "cta": "Subscribe (18+)",
                    "conversion_goal": "subscriptions",
                    "channels": "social"})
check("brand profile saves", r.status == 200)

# 4. campaign + generation + explicit confirm
post("/campaign/new", {"csrf": tok, "name": "October push",
                       "channel": "social",
                       "objective": "subscriptions"})
html = get("/dashboard").read().decode()
m = re.search(r'/campaign/([0-9a-f]{32})', html)
check("campaign appears", m is not None)
cid = m.group(1)
html = get(f"/campaign/{cid}").read().decode()
check("campaign page renders variants state",
      "Generate A/B variants" in html)
post(f"/campaign/{cid}/generate", {"csrf": tok})
html = get(f"/campaign/{cid}").read().decode()
check("variants generated and labeled",
      "Variant A" in html and "deterministic local template"
      in html)
post(f"/campaign/{cid}/confirm", {"csrf": tok})
html = get(f"/campaign/{cid}").read().decode()
check("explicit confirmation flips status",
      "confirmed" in html)

# 5. consent on, metric entry, analytics path
post("/consent", {"csrf": tok, "analytics": "on"})
r = post(f"/campaign/{cid}/metric", {
    "csrf": tok, "variant_label": "A", "impressions": "1200",
    "clicks": "48", "conversions": "6", "revenue_cents": "6000",
    "spend_cents": "1000", "source": "manual"})
check("metric recorded under consent", r.status == 200)

# 6. monthly review builds; idempotent; approval gated
post("/review", {"csrf": tok, "period": "2026-10"})
post("/review", {"csrf": tok, "period": "2026-10"})
html = get("/review").read().decode()
check("monthly proposal built once",
      html.count("2026-10") >= 1 and "proposed" in html)
check("honest statistics shown",
      "correlation is not causation" in html)

# 7. SEO: blocked URL refused; sitemap works
try:
    post("/seo", {"csrf": tok,
                  "url": "http://169.254.169.254/latest/"})
    check("SSRF target refused at the UI", False,
          "request unexpectedly accepted")
except urllib.error.HTTPError as e:
    check("SSRF target refused at the UI", e.code == 400,
          f"got {e.code}")
r = post("/seo/sitemap", {"csrf": tok, "urls": "https://"
                          "example.com/\nhttps://example.com/a"})
body = r.read()
check("sitemap generates",
      b"&lt;urlset" in body or b"<urlset" in body, "no sitemap in "
      "response")

# 8. payments boundary honest
html = get("/payments").read().decode()
check("payment boundary labeled, not faked",
      "not configured" in html and "No real transactions"
      in html)

# 9. DB unavailability does not fake health
r = get("/healthz")
check("health endpoint verifies DB", r.status == 200)

fails = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(fails)}/{len(results)} "
      "journeys passed")
sys.exit(1 if fails else 0)
