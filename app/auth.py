"""Argon2id passwords, opaque hashed session tokens, login
throttling, CSRF."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .db import session
from .models import AuthSession, LoginAttempt, User

_ph = PasswordHasher()  # argon2id, per-hash salt, constant-time
SESSION_TTL = timedelta(hours=12)
MAX_FAILURES = 10
WINDOW = timedelta(minutes=15)


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(stored: str, pw: str) -> bool:
    try:
        return _ph.verify(stored, pw)
    except VerifyMismatchError:
        return False


def _key(email: str, ip: str) -> str:
    return hashlib.sha256(
        f"{email}|{ip}".encode()).hexdigest()[:64]


def login(email: str, password: str, ip: str = "-"):
    """Returns (user, token) or raises PermissionError with the
    throttling reason. Failures are always recorded."""
    db = session()
    now = datetime.now(timezone.utc)
    key = _key(email, ip)
    recent = db.query(LoginAttempt).filter(
        LoginAttempt.key == key,
        LoginAttempt.success.is_(False),
        LoginAttempt.at >= now - WINDOW).count()
    if recent >= MAX_FAILURES:
        db.add(LoginAttempt(key=key, success=False))
        db.commit()
        raise PermissionError("too many attempts; try later")
    user = db.query(User).filter(
        User.email == email.lower().strip()).one_or_none()
    if user is None or not verify_password(
            user.password_hash, password):
        db.add(LoginAttempt(key=key, success=False))
        db.commit()
        raise PermissionError("invalid credentials")
    db.add(LoginAttempt(key=key, success=True))
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=now + SESSION_TTL))
    db.commit()
    return user, token


def user_for_token(token: str):
    if not token:
        return None
    db = session()
    row = db.get(AuthSession,
                 hashlib.sha256(token.encode()).hexdigest())
    if row is None or row.expires_at < datetime.now(timezone.utc):
        return None
    return db.get(User, row.user_id), row


def logout(token: str):
    db = session()
    db.query(AuthSession).filter(
        AuthSession.token_hash == hashlib.sha256(
            token.encode()).hexdigest()).delete()
    db.commit()
