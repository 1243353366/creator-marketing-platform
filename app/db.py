"""SQLAlchemy wiring. One engine per process; Postgres only."""
from sqlalchemy import create_engine
from sqlalchemy.orm import (DeclarativeBase, scoped_session,
                            sessionmaker)

_Engine = None
_Session = None


class Base(DeclarativeBase):
    pass


def init_db(url, echo=False):
    """Thread-safe scoped sessions; the app context removes them
    (see create_app), so pooled connections are always returned."""
    global _Engine, _Session
    _Engine = create_engine(url, echo=echo, future=True,
                             pool_pre_ping=True)
    _Session = scoped_session(sessionmaker(
        bind=_Engine, expire_on_commit=False))


def session():
    return _Session()


def close_session():
    """Remove this thread's scoped session and return its pooled
    connection. Called on app-context teardown."""
    _Session.remove()
