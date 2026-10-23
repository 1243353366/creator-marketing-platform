# Claim ledger — honest feature status

## Implemented and tested (evidence: tests/test_platform.py, 14
## passing; scripts/journey_verify.py, 15/15 against a running
## server)

- Fail-closed boot (no Postgres / weak secret)
- Signup/login with Argon2id; login throttling
- Session management with hashed tokens; CSRF on all forms
- Tenant isolation (cross-tenant reads 404; tested)
- Consent separation (analytics vs training); withdrawal
  deletes metrics
- Campaign creation, generation (labeled template engine),
  explicit publication confirmation
- Server-side output validation incl. banned terms and
  revenue-promise rejection
- Provider failure states (unavailable/timeout/malformed/
  over-budget) — visible errors, no fabrication
- SEO: SSRF-hardened fetching, page audit with issues, sitemap
  generate/validate, audit history persistence
- Payment webhook verification (HMAC + replay + idempotency)
- Monthly pipeline: consent gate, dedup, Wilson intervals,
  minimum sample, idempotent per period, approval workflow
- Rate-limited generation

## Implemented but not fully tested

- Provider-integrated generation (no live provider configured;
  failure paths tested against an unreachable endpoint only)
- SEO audit against real external sites (SSRF refusal tested
  exhaustively; live fetch exercised only against example.com)

## Partially implemented

- Alembic migrations are initialized; CI applies them, but
  long-term drift management is manual.

## Not implemented

- Google Search Console data fetch (design documented; needs
  your OAuth/service-account credentials)
- Subscription checkout flows (provider terms and approval
  required first)
- Model training / fine-tuning path (consent plumbing exists;
  no training pipeline)

## Blocked by missing credentials, approval, or infrastructure

- AI provider integration (needs AI_PROVIDER_URL + key)
- Search Console integration (needs your domain verification
  and authorized credentials)
- Payment provider (needs a provider whose acceptable-use terms
  permit the actual business model, plus account approval)

## Verified NOT done / explicitly out of scope

- No Google Indexing API usage
- No fake checkout, no fake indexing claims, no simulated
  provider success
- No browser-automation evidence: the sandbox's remote browser
  cannot reach the local server; verification was HTTP-level
  (all 15 journeys) with the exact URL and launch commands given.
