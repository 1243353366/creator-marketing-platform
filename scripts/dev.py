"""Development launcher: boots an embedded real PostgreSQL,
exports DATABASE_URL/SECRET_KEY, and runs the Flask server.

    python3 scripts/dev.py

In production, set DATABASE_URL (postgresql://...), SECRET_KEY,
and run with a WSGI server (see README).
"""
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pgserver import PostgresServer  # noqa: E402

PGDATA = Path("/tmp/cmp-dev-pgdata")
PGDATA.mkdir(parents=True, exist_ok=True)
server = PostgresServer(PGDATA)
os.environ["DATABASE_URL"] = server.get_uri()
os.environ.setdefault("SECRET_KEY", "dev-only-" + secrets.
                      token_urlsafe(24))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

_cfg = Config(str(ROOT / "alembic.ini"))
_cfg.set_main_option("script_location",
                     str(ROOT / "migrations"))
command.upgrade(_cfg, "head")
print("PostgreSQL ready; migrations applied to head.")
from app import create_app
app = create_app()
app.run(host="127.0.0.1", port=7778, threaded=True)
