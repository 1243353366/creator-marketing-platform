# Workers edition (D1-persisted)

The live worker at https://creator-marketing-platform.aadishankar1999.workers.dev
was originally deployed with an in-memory store: POST and GET could land on
different edge isolates, so consent granted in one request was invisible to
the next. This edition replaces the in-memory Maps with a strongly consistent
Durable Object (SQLite storage), making
consent gating, tenant isolation, script storage, and pipeline idempotency
actually work across isolates. It is a faithful port of the deployed API
surface (`live-worker-export.js`, fetched from the live script via the API).

Provider chain for /api/generate: external AI provider (if
AI_PROVIDER_URL/AI_PROVIDER_KEY secrets exist) -> Workers AI
(@cf/meta/llama-3.1-8b-instruct-fp8, free-tier model verified on this account)
-> labeled template. Output is always honestly labeled with its source.

## Deploy (requires a Workers-write credential)

    npx wrangler deploy

The store ships inside the script (a Durable Object class) - no separate
D1/KV permission or provisioning step is needed. DO was chosen over KV
because KV is eventually consistent (consent grants invisible up to 60s
on other edges) and over D1 because D1 requires a separate Edit permission.
A probe DO was verified executing on this account's free plan first.

`wrangler.jsonc` uses the SAME worker name as the live deployment, so this
replaces the running worker and keeps its enabled workers.dev route. D1 is
auto-provisioned on deploy when the binding has no database_id.
See .github/workflows/deploy-workers.yml for the automated version with a
post-deploy smoke test (health, consent persistence, pipeline idempotency,
SSRF block, tenant isolation).
