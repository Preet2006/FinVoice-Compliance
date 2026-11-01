"""Password and server-managed session helpers."""

from datetime import datetime, timedelta
import hashlib
import secrets
from typing import Optional

from argon2 import PasswordHasher
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppUser, AuthSession

SESSION_COOKIE = "shaha_session"
CSRF_COOKIE = "shaha_csrf"
CSRF_HEADER = "X-CSRF-Token"

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except Exception:
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: AppUser) -> tuple[str, str, AuthSession]:
    now = datetime.utcnow()
    raw_session = secrets.token_urlsafe(48)
    raw_csrf = secrets.token_urlsafe(32)
    session = AuthSession(
        id=secrets.token_urlsafe(18),
        user_id=user.id,
        token_hash=_hash_token(raw_session),
        csrf_token_hash=_hash_token(raw_csrf),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.auth_session_hours),
    )
    db.add(session)
    db.commit()
    return raw_session, raw_csrf, session


def get_session(db: Session, raw_token: Optional[str]) -> Optional[AuthSession]:
    if not raw_token:
        return None
    session = db.query(AuthSession).filter(AuthSession.token_hash == _hash_token(raw_token)).first()
    if not session or session.expires_at <= datetime.utcnow() or not session.user.is_active:
        return None
    return session


def invalidate_session(db: Session, raw_token: Optional[str]) -> None:
    session = get_session(db, raw_token)
    if session:
        db.delete(session)
        db.commit()


def csrf_valid(session: AuthSession, token: Optional[str]) -> bool:
    return bool(token) and secrets.compare_digest(session.csrf_token_hash, _hash_token(token))
