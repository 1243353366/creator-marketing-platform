# Failure ledger: mistakes repurposed as condition models

Every failure below happened in this repository's build sessions.
Each is recorded as a reusable condition — the situation that
triggers it, the symptom, the cause, and the standing guard that
now prevents it. New failures get appended here; nothing is
deleted.

## L1. Model/migration constraint drift

- **Condition:** a CHECK-constrained column (e.g. `status`)
  gains new allowed values in the ORM model.
- **Symptom:** `CheckViolation` at runtime from the migrated
  database while model-built test databases pass, or the reverse.
- **Cause:** the constraint exists in both the model and the
  initial migration; a model edit alone leaves the live DB's
  constraint behind (2026-10-23, `ck_review_status`).
- **Guard:** schema parity guard (below) + a rule: constraint
  changes ship as an explicit DROP/ADD migration.

## L2. create_all is not a migration

- **Condition:** dev or test startup builds schema with
  `Base.metadata.create_all` while production uses migrations.
- **Symptom:** `UndefinedColumn` on the migrated/dev database the
  moment a column is added to the model (2026-10-23, dev server
  500s on brand page after the checkout_url addition).
- **Cause:** create_all never alters existing tables.
- **Guard:** `scripts/dev.py` is migration-driven
  (`alembic upgrade head`); CI runs migrations against real
  PostgreSQL before tests.

## L3. Unscoped test queries across tenants

- **Condition:** a test looks up a row (profile, review) without
  filtering by the test's own user/tenant.
- **Symptom:** passes in isolation, fails in the full suite —
  the query silently picks up another test's row.
- **Cause:** the suite shares one database; `.first()` on an
  unscoped query is a race with test order (2026-10-23, twice).
- **Guard:** test lookups are scoped by the creating user's id
  (pattern in tests/test_platform.py); the payment test asserts
  cross-tenant isolation directly.

## L4. Mid-request crashes poison the scoped session

- **Condition:** a request raises after the session has begun
  work (e.g. a stale `url_for` endpoint).
- **Symptom:** an UNRELATED later test fails with data that looks
  never-saved; the crash itself is the earlier failure in the log.
- **Cause:** the thread-scoped session is left in a broken
  transaction; the BuildError at one endpoint cascaded into a
  checkout test asserting `None` (2026-10-23).
- **Guard:** when suite failures look causally impossible, check
  for an EARLIER crash in file order first. Endpoints are covered
  by the journey smoke suite which exercises every route.

## L5. A green guard is not a working guard (mutation-drill rule)

- **Condition:** any check written to catch a failure class.
- **Symptom:** the check passes — while the failure it exists for
  sails through.
- **Cause:** the first parity guard built both schemas on one
  PostgresServer; `get_uri()` returns the SAME database on every
  call, so the models path ran `create_all` against the
  already-migrated database, which never alters existing tables.
  Injected drift (a model column with no migration) passed green
  (2026-10-23).
- **Guard:** every new guard is drilled with an injected failure
  BEFORE it is trusted; the parity guard now uses two isolated
  servers and the drill (inject -> catch -> revert -> clean) is
  the acceptance test for changes to it.

## L6. Anchored edits must assert their anchor

- **Condition:** scripted code edits (`str.replace` patches).
- **Symptom:** a silent no-op edit, discovered much later.
- **Cause:** anchors drift as code changes; replace without
  assert succeeds on nothing.
- **Guard:** every scripted patch asserts the anchor exists
  before replacing (pattern used throughout this ledger's
  scripts). A failed assert is a loud, early, cheap error.

## L7. Membership tested against the wrong kind of set

- **Condition:** a policy gate that switches representation —
  one branch's `allowed` holds FLOWS, another's holds PROVIDER
  NAMES — while the membership check stays written for the
  first kind.
- **Symptom:** the gate refuses a permitted pairing (segpay for
  adult checkout) — or, in the mirrored bug, would permit a
  forbidden one.
- **Cause:** writing `if flow not in allowed` where `allowed`
  was a set of providers. The Oracle class in miniature: the
  right check executed against the wrong target set (2026-10-23,
  minutes after designing the sentinel for exactly this class).
- **Guard:** the checkout branch now names its check explicitly
  (`provider not in CHECKOUT_POLICY[category]`) instead of
  reusing a shared condition; both permitted AND refused
  pairings are asserted per category in the test suite, so
  either direction of the bug fails a test.

## L8. A probe that silently skips its own attacks

- **Condition:** an attack script discovers its target ids by
  pattern-matching rendered pages (or any heuristic) and
  continues when the match finds nothing.
- **Symptom:** the probe reports all attacks repelled - while
  the IDOR attack never ran once, because the id-shape regex
  (\\d+) matched zero hex-uuid campaign ids. Verified by the
  L5 drill: an injected unscoped query sailed through green.
- **Cause:** the sentinel guessed the target's shape instead
  of reading it - the Oracle class aimed at the sentinel
  itself. Twice in one hour (csrf field name assumed
  'csrf_token', ids assumed decimal).
- **Guard:** probe extraction uses the page's own hrefs; a
  probe that cannot find its attack targets exits nonzero
  instead of reporting success; the drill (inject scope
  removal -> probe must fail -> revert -> clean) is the
  acceptance test for every probe change, run in CI.
