# Implementation Plan and Threat Model — Creator Marketing Platform

Written before substantial changes, per operating rules.

## 1. Mission and scope

A creator-marketing SaaS: creators configure brand voice, audience,
channel, and intensity; the platform generates marketing scripts and
A/B variants with server-side validation; tracks campaign performance
under explicit, separately-scoped consent; audits SEO/indexing
health; and produces monthly, evidence-linked improvement proposals
that require human approval before changing anything.

Honest scope note: this build delivers the complete core product
(accounts, tenants, consent, generation, analytics, SEO auditing,
monthly pipeline, payments boundary) with real code and tests.
Integrations that require external credentials (AI provider, Google
Search Console, payment provider) ship as clearly labeled
integration boundaries with setup instructions, not fakes.

## 2. Stack (chosen for the sandbox, production-shaped)

- Python 3.11, Flask + Jinja2 (server-rendered, small attack surface)
- SQLAlchemy 2.x ORM with Alembic versioned migrations
- PostgreSQL in development AND production. Development uses an
  embedded real PostgreSQL (pip `pgserver`); production uses any
  Postgres via DATABASE_URL. No SQLite fallback exists anywhere:
  the app refuses to start without a Postgres connection.
- Argon2id (argon2-cffi) password hashing
- Sessions: signed, HttpOnly, SameSite=Lax cookies; server-side
  session store with expiry

## 3. Architecture and data model

Single Flask app, blueprint-per-feature, layering:

    HTTP route -> service layer -> repository (SQLAlchemy) -> Postgres

Every tenant-owned query is filtered by tenant_id at the repository
layer AND constrained at the database layer (RLS-style policy via
per-tenant check constraints + query scopes). A user IS a tenant
(one creator per account in v1; explicit tenant_id on every row).

Core tables: users, sessions, consents, brand_profiles, campaigns,
variants, generation_jobs, performance_metrics, seo_audits,
seo_pages, monthly_reviews, experiments, audit_log, webhook_events.

## 4. Threat model (STRIDE + abuse cases)

Assets: credentials, session cookies, creator campaign data,
consent state, analytics data, SEO audit results, payment
references.

Trust boundaries:
  T1 internet -> Flask (untrusted input: auth, generation
     settings, SEO URLs, webhook payloads, AI provider output)
  T2 app -> Postgres (SQL injection, tenant leakage)
  T3 app -> external fetch (SSRF via SEO audit URLs)
  T4 app -> AI provider (prompt injection via imported docs)
  T5 app -> payment provider (webhook forgery, replay)

Attacker capabilities considered: unauthenticated web attacker,
authenticated cross-tenant attacker (changes IDs in requests), CSRF,
prompt injection through pasted documents, SSRF to cloud metadata
(169.254.169.254), webhook forger without the signing secret,
session theft via cookie misconfiguration, brute-force login.

Mitigations (each has a test):
  - Argon2id + per-hash salt + constant-time verify (test_auth)
  - Login rate limit per account + per IP (test_auth)
  - Every repository call takes tenant_id from the session, never
    from the request; cross-tenant access test asserts 404/403
  - CSRF token on every state-changing form (test_form_csrf)
  - SEO fetch: allowlist http/https, DNS-resolve and reject
    private/reserved/loopback/metadata ranges, revalidate on every
    redirect hop, timeouts, response size cap (test_seo_ssrf)
  - Generation: creator instructions and imported text are stored
    and sent as DATA, never executed; provider output re-validated
    against the output schema; provider outage/malformed/budget
    produce visible error states, never fabricated content
    (test_generation_failures)
  - Webhooks: HMAC signature verify + timestamp replay window +
    event-id idempotency (test_webhook)
  - Consent: analytics_consent and training_consent are separate
    rows; analytics queries filter on consent; withdrawal marks
    derived data for deletion (test_consent)
  - No secrets in code/URLs/logs; secret scanning in CI
  - Fail-closed: missing DATABASE_URL / secret key / provider
    credentials disable the dependent feature loudly (test_failclosed)

Residual risks (documented, not silently accepted): provider-side
data handling is out of our control (documented per-provider);
Google indexing cannot be guaranteed (documented limitation);
payments remain a boundary until provider approval is obtained.

## 5. Monthly improvement pipeline

Deterministic job: collects consented metrics for the period,
validates completeness/provenance/duplicates, computes CTR /
conversion rate / revenue-per-spend with Wilson 95% intervals,
requires a minimum sample before any comparison is proposed,
compares like-for-like (same channel/objective), writes a versioned
proposal (evidence, limitations, risks), and requires an
authorized approval before anything changes. Idempotent on
(tenant, period): reruns update-or-ignore, never duplicate.
Failures leave historical records untouched (transactional).

## 6. CI and gates

GitHub Actions: ruff lint, unit + integration tests against real
embedded PostgreSQL, migration check, security regression suite,
secret scan. Least-privilege permissions, no secrets printed.

## 7. Build order

1. Scaffold + this plan [this commit]
2. Schema/migrations + persistence with tenant scoping
3. Auth + sessions + consent
4. Marketing engine + generation failure handling
5. SEO module with SSRF hardening
6. Analytics + monthly pipeline
7. Payments webhook boundary
8. Web UI journeys + browser verification
9. Docs (README/SECURITY/claim ledger) + CI + push
