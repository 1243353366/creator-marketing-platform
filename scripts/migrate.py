"""Run Alembic migrations against an embedded real PostgreSQL.

    python3 scripts/migrate.py upgrade
    python3 scripts/migrate.py revision "message"

pgserver stops its PostgreSQL when this process exits, so the
server and the migration command deliberately live in the same
process. For a production PostgreSQL, set DATABASE_URL and call
alembic directly; this script only fills it in when absent.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if not os.environ.get("DATABASE_URL"):
    from pgserver import PostgresServer

    p = Path("/tmp/cmp-mig-pg")
    p.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_URL"] = PostgresServer(p).get_uri()

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

cfg = Config(str(ROOT / "alembic.ini"))
cfg.set_main_option("script_location", str(ROOT / "migrations"))
mode = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
if mode == "revision":
    command.revision(
        cfg, message=" ".join(sys.argv[2:]) or "revision",
        autogenerate=True)
else:
    command.upgrade(cfg, "head")
    print("migration: upgrade head applied")
