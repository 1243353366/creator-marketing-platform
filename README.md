# Creator Marketing Platform

A production-oriented creator-marketing SaaS: brand voice and
intensity configuration, marketing script generation with honest
failure states, consent-gated performance analytics, SSRF-hardened
SEO auditing, and an idempotent monthly improvement pipeline that
never changes anything without explicit approval.

## Product scope and architecture

- Python 3.11, Flask (server-rendered), SQLAlchemy 2.x, PostgreSQL
- Layering: route -> service -> repository; tenant identity comes
  only from the server-side session, never from the request
- Security posture: Argon2id passwords, CSRF on every state
  change, per-tenant query scoping, SSRF-hardened fetching,
  fail-closed configuration

## Prerequisites

- Python 3.11+, pip
- PostgreSQL. For local development `pgserver` (pip) provides a
  REAL embedded PostgreSQL — no SQLite fallback exists anywhere
  in this codebase, by design.

## Environment variables

| Variable | Purpose | Required |
|---|---|---|
| DATABASE_URL | Postgres, e.g. postgresql://user:pw@host/db | yes (app refuses to boot otherwise) |
| SECRET_KEY | Flask session signing, >= 16 chars | yes (app refuses to boot otherwise) |
| AI_PROVIDER_URL | OpenAI-compatible endpoint | no; without it generation uses the labeled template engine |
| AI_PROVIDER_KEY | Provider API key | no (same as above) |
| PAYMENT_WEBHOOK_SECRET | HMAC secret for payment webhooks | no; the payments page is a labeled boundary without it |

Secrets are never committed, logged, or placed in URLs.

## Local setup and launch

```bash
pip install -r requirements-dev.txt   # or requirements.txt for production deps
python3 scripts/dev.py                # boots embedded PostgreSQL, applies schema, serves on 127.0.0.1:7778
```

Browser: http://127.0.0.1:7778 — health endpoint: /healthz

Journey verification against a running server:

```bash
python3 scripts/journey_verify.py http://127.0.0.1:7778
```

## Tests and CI (exact commands)

```bash
python3 -m pytest tests/ -q          # 14 tests against real embedded PostgreSQL
python3 -m ruff check app tests     # lint
```

CI (GitHub Actions) runs the same: lint, tests on real
PostgreSQL, migration check, journey smoke. Least-privilege
permissions; no secrets printed.

## What the tests prove

- Fail-closed boot (no Postgres / weak secret key refused)
- Login throttle after repeated failures; Argon2id verification
- CSRF required on state changes
- Cross-tenant reads return 404 even with a valid session
- Consent: metrics refused without analytics consent; withdrawal
  deletes recorded metrics; training consent is separate
- Generation: template variants are labeled as NOT AI output;
  banned and revenue-promise content rejected server-side;
  provider outages produce visible errors, never fabricated text
- SSRF: loopback, private, link-local, metadata, and file:// all
  refused in SEO fetching; redirects revalidated
- Payment webhook: HMAC verified, stale refused, replay refused,
  missing secret fails closed
- Monthly pipeline: consent-gated, idempotent per period,
  Wilson-interval comparisons, no winner on raw rates

## Google Search Console setup and limitations

Search Console integration requires your own authorization:
1. Verify your domain in Search Console (HTML file or DNS).
2. Create OAuth credentials or a service account with Search
   Console access; store the JSON key in your secret manager.
3. Configure the redirect URIs over HTTPS in production.

Limitations, stated plainly:
- A crawl request is not a guarantee of crawling or indexing.
- Public page checks and authenticated Search Console data are
  separate data sources.
- Never claim a page is indexed from an HTTP 200 or an API
  success alone. The platform does not use Google's Indexing API
  as a general endpoint.
- No live Google API calls are made from tests.

## Payment-provider configuration and restrictions

THREE separate flows with separate provider rules. The platform
is category-neutral: creators market anything (business, art,
courses, content), set their own paywall link, and provider
rules attach to the CREATOR'S DECLARED CATEGORY:
(1) SaaS subscriptions paid to us (software processor — Stripe
suits this flow, the SaaS sells marketing software), (2) fans
paying creators (category-aware registry: general categories may
use standard processors; adult content requires a processor that
explicitly permits it — Segpay / CCBill / Verotel candidates
with written approval; Cash App, Venmo, PayPal, Stripe are
prohibited or restricted for adult — verified 2026-10-23), and
(3) creator payouts (their own revenue, paid out by the approved
processor or a compliant payout provider). Full matrix and the
marketplace-model written-approval requirement:
docs/PAYMENTS.md. A policy-enforced provider registry
(app/payments.py) refuses structurally disallowed pairings; an
unconfigured flow refuses to create a checkout (nothing faked).
Creator funds are never collected or held (https-only link-out).
Paid access unlocks ONLY on a server-verified payment record
(verified_payments), never a submitted handle or screenshot.
Setup: provider approval in writing, PAYMENT_WEBHOOK_SECRET,
register the signed webhook endpoint /webhook/payment. Webhooks
verify HMAC signatures with replay protection and idempotent
event handling. Card data is never stored; provider-hosted
checkout is the required pattern. No transactions are simulated.

## Failure ledger and schema parity

- docs/LESSONS.md: build-session mistakes recorded as reusable
  condition models with their standing guards. Appended, never
  rewritten.
- scripts/check_schema_parity.py: builds the schema from
  migrations only and from models only on two isolated real
  PostgreSQL instances and fails on any drift (tables, columns,
  check constraints). Runs in CI on every push. Every guard is
  drilled with an injected failure before it is trusted.

## Consent and data retention

- Analytics consent and model-training consent are separate
  grants; training never happens without its own grant.
- Withdrawing analytics consent immediately deletes that
  tenant's recorded metrics.
- Only campaign-level, pseudonymous performance data is stored;
  no end-customer identities are collected.

## Security controls and threat model

See SECURITY.md. Controls: Argon2id, CSRF tokens, scoped
sessions, per-tenant repository scoping, SSRF-hardened fetch
with redirect revalidation and response caps, parameterized
queries throughout, login rate limiting, fail-closed config.

## Known gaps and limitations

See CLAIM-LEDGER.md for the honest, per-feature status ledger.

## Deployment, backup, restore, rollback

- Production: set DATABASE_URL and SECRET_KEY (secret store),
  run migrations, serve with a WSGI server (e.g. gunicorn) with
  TLS termination in front; /healthz is the readiness check.
- Backup: standard pg_dump per your retention policy.
- Restore: restore dump, run app; schema is forward-compatible.
- Rollback: redeploy the previous image; Postgres migrations are
  additive in this release, so a rollback does not require a
  schema downgrade.
