"""Test configuration: a REAL embedded PostgreSQL server. There
is no SQLite fallback in tests either, by design."""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pgserver import PostgresServer  # noqa: E402

from pathlib import Path as _P
_pgd = _P("/tmp/cmp-pgdata")
_pgd.mkdir(parents=True, exist_ok=True)
_PG = PostgresServer(_pgd)
DB_URI = _PG.get_uri()

from app.db import Base  # noqa: E402
import app.models  # noqa: E402,F401

_engine = create_engine(DB_URI)
Base.metadata.drop_all(_engine)
Base.metadata.create_all(_engine)


@pytest.fixture()
def client():
    from app import create_app
    app = create_app(config={
        "DATABASE_URL": DB_URI,
        "SECRET_KEY": "test-secret-key-" + "x" * 16,
        "TESTING": True,
    })
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def make_user(client, email, password="password-12345"):
    r = client.post("/signup", data={
        "email": email, "password": password},
        follow_redirects=True)
    assert r.status_code == 200, r.status_code
    return client


def csrf_of(client):
    from app import auth
    from flask import session as fsession
    data = auth.user_for_token(fsession.get("token", ""))
    return data[1].csrf_token
