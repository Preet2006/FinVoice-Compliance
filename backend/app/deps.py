"""Authentication, CSRF and request-scoped authorization dependencies."""

from dataclasses import dataclass
from typing import Generator, Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import AppUser, AuthSession
from app.services.auth_service import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, csrf_valid, get_session

VALID_ROLES = {"caller", "manager", "head"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class RoleContext:
    role: str
    user_id: str
    user: AppUser


def _unauthenticated() -> HTTPException:
    return HTTPException(status_code=401, detail={"error": {"code": "AUTHENTICATION_REQUIRED", "message": "Authentication is required."}})


def get_current_session(request: Request, db: Session = Depends(get_db)) -> Optional[AuthSession]:
    session = get_session(db, request.cookies.get(SESSION_COOKIE))
    if not session:
        return None
    if request.method not in SAFE_METHODS:
        # The cookie is intentionally not accepted as proof: a cross-site
        # request automatically carries it. The browser must echo it in the
        # custom header so same-origin policy/CSRF protection is meaningful.
        csrf = request.headers.get(CSRF_HEADER)
        if not csrf_valid(session, csrf):
            raise HTTPException(status_code=403, detail={"error": {"code": "CSRF_REQUIRED", "message": "A valid CSRF token is required."}})
    return session


def get_current_user(session: Optional[AuthSession] = Depends(get_current_session)) -> AppUser:
    if session is None:
        raise _unauthenticated()
    return session.user


def get_role_context(
    request: Request,
    db: Session = Depends(get_db),
    session: Optional[AuthSession] = Depends(get_current_session),
    x_role: Optional[str] = Header(default=None, alias="X-Role"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
) -> Generator[RoleContext, None, None]:
    """Use the validated session in production.

    The legacy-header branch exists only when explicitly enabled by the
    offline pytest fixture, preserving old test fixtures while production
    requests cannot forge identity from X-Role/X-User-Id.
    """
    if session is not None:
        user = session.user
    elif settings.allow_legacy_headers and x_user_id:
        user = db.query(AppUser).filter(AppUser.id == x_user_id).first()
        if user is None:
            raise HTTPException(status_code=400, detail={"error": {"code": "UNKNOWN_USER", "message": "Unknown test user."}})
        if x_role and user.role != x_role.strip().lower():
            raise HTTPException(status_code=400, detail={"error": {"code": "ROLE_MISMATCH", "message": "Role does not match the test user."}})
    else:
        raise _unauthenticated()
    if user.role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail={"error": {"code": "INVALID_ACCOUNT_ROLE", "message": "This account has no valid application role."}})
    yield RoleContext(role=user.role, user_id=user.id, user=user)
