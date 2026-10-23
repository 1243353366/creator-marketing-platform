"""SQLAlchemy wiring. One engine per process; Postgres only."""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_Engine = None
_Session = None


class Base(DeclarativeBase):
    pass


def init_db(url, echo=False):
    global _Engine, _Session
    _Engine = create_engine(url, echo=echo, future=True)
    _Session = sessionmaker(bind=_Engine, expire_on_commit=False)


def session():
    return _Session()
