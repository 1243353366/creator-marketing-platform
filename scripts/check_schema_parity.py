"""Schema parity guard: migrations and models must agree.

Failure condition (observed 2026-10-23, twice in one session):
the ORM models said a column/constraint existed, the migration
history did not create it (or vice versa). Tests passed on fresh
model-built databases while the migrated one broke at runtime
with UndefinedColumn / CheckViolation.

This script builds the schema TWO ways on a real PostgreSQL:

  A. migrations only: alembic upgrade head from an empty database
  B. models only:     Base.metadata.create_all

then introspects both through information_schema and fails with a
readable diff if tables, columns, or check constraints disagree.
A third check verifies every model table exists after `alembic
upgrade head` on a FRESH database — the create_all path must never
become a load-bearing shortcut.

Exit codes: 0 = parity, 1 = drift (with the diff printed).
Used in CI on every push; locally via scripts/check_schema_parity.py.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PGINSTALL", "/app/pginstall")

from pgserver import PostgresServer  # noqa: E402


def introspect(url):
    from sqlalchemy import create_engine, text
    eng = create_engine(url)
    # alembic_version is migration bookkeeping, not product schema
    out = {"tables": {}, "checks": set()}
    SKIP = {"alembic_version"}
    with eng.connect() as c:
        rows = c.execute(text(
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema='public' ORDER BY 1, 2"))
        for t, col, dt, nul in rows:
            if t in SKIP:
                continue
            out["tables"].setdefault(t, []).append(
                (col, dt, nul))
        rows = c.execute(text(
            "SELECT tc.table_name, cc.check_clause FROM "
            "information_schema.check_constraints cc JOIN "
            "information_schema.table_constraints tc ON "
            "cc.constraint_name = tc.constraint_name "
            "WHERE tc.constraint_schema='public'"))
        for t, clause in rows:
            if t in SKIP:
                continue
            clause = " ".join(clause.split())
            out["checks"].add(f"{t}: {clause}")
    eng.dispose()
    return out


def main():
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="cmp-parity-")
    try:
        # TWO ISOLATED servers: get_uri() returns the SAME database
        # on every call for a given server, so a second get_uri()
        # is NOT a second database. One server here would silently
        # make path B create_all() against the already-migrated
        # database (create_all never alters existing tables) and
        # the guard would pass vacuously. Found by mutation drill
        # 2026-10-23: injected drift sailed through a green check.
        pgroot_a = Path(tmp) / "pg_a"
        pgroot_b = Path(tmp) / "pg_b"
        pgroot_a.mkdir(parents=True, exist_ok=True)
        pgroot_b.mkdir(parents=True, exist_ok=True)
        url_a = PostgresServer(pgroot_a).get_uri()
        url_b = PostgresServer(pgroot_b).get_uri()
        os.environ["DATABASE_URL"] = url_a

        from alembic import command
        from alembic.config import Config
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location",
                            str(ROOT / "migrations"))
        command.upgrade(cfg, "head")
        a = introspect(url_a)

        from app.db import Base
        import app.models  # noqa: F401  register all tables
        from sqlalchemy import create_engine
        eng_b = create_engine(url_b)
        Base.metadata.create_all(eng_b)
        b = introspect(url_b)
        eng_b.dispose()

        problems = []
        tables_a, tables_b = set(a["tables"]), set(b["tables"])
        for t in sorted(tables_a - tables_b):
            problems.append(f"table {t!r}: in MIGRATIONS, not in MODELS")
        for t in sorted(tables_b - tables_a):
            problems.append(f"table {t!r}: in MODELS, not in MIGRATIONS")
        for t in sorted(tables_a & tables_b):
            cols_a = dict((c, d) for c, d, _ in a["tables"][t])
            cols_b = dict((c, d) for c, d, _ in b["tables"][t])
            for c in sorted(set(cols_a) - set(cols_b)):
                problems.append(
                    f"column {t}.{c}: in MIGRATIONS, not in MODELS")
            for c in sorted(set(cols_b) - set(cols_a)):
                problems.append(
                    f"column {t}.{c}: in MODELS, not in MIGRATIONS")
        checks_a, checks_b = a["checks"], b["checks"]
        for ck in sorted(checks_a - checks_b):
            problems.append(f"check constraint only in MIGRATIONS: {ck}")
        for ck in sorted(checks_b - checks_a):
            problems.append(f"check constraint only in MODELS: {ck}")

        if problems:
            print("SCHEMA PARITY FAILURE: migrations and models "
                  "disagree.\nA deployment would break at runtime even "
                  "if tests pass. Fix by writing a migration:\n")
            for p in problems:
                print("  -", p)
            return 1
        n = len(tables_a)
        print(f"schema parity OK: {n} tables, "
              f"{len(a['checks'])} check constraints identical "
              "via migrations and models")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
