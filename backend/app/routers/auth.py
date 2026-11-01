"""Login, session restoration, logout and CSRF-protected authentication."""

from collections import defaultdict
import time
from threading import Lock

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import AppUser
from app.services.auth_service import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    create_session,
    invalidate_session,
    verify_password,
    get_session,
    csrf_valid,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
_attempts: dict[str, list[float]] = defaultdict(list)
_attempts_lock = Lock()


class LoginRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=512)


class AuthUserOut(BaseModel):
    id: str
    name: str
    role: str
    office_id: str


def _auth_user(user: AppUser) -> dict:
    return {"id": user.id, "name": user.name, "role": user.role, "office_id": user.office_id}


def _client_key(request: Request) -> str:
    # Do not log or return this value. It is only an in-process limiter key.
    return request.client.host if request.client else "unknown"


def _rate_limited(request: Request) -> bool:
    now = time.monotonic()
    cutoff = now - settings.auth_login_window_seconds
    key = _client_key(request)
    with _attempts_lock:
        recent = [t for t in _attempts[key] if t >= cutoff]
        _attempts[key] = recent
        return len(recent) >= settings.auth_login_max_attempts


def _record_attempt(request: Request) -> None:
    with _attempts_lock:
        _attempts[_client_key(request)].append(time.monotonic())


def _set_auth_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    common = {"httponly": True, "secure": settings.auth_cookie_secure, "samesite": "lax", "path": "/"}
    response.set_cookie(SESSION_COOKIE, session_token, max_age=settings.auth_session_hours * 3600, **common)
    # CSRF token must be readable by the browser so the API client can echo it.
    response.set_cookie(CSRF_COOKIE, csrf_token, max_age=settings.auth_session_hours * 3600,
                        httponly=False, secure=settings.auth_cookie_secure, samesite="lax", path="/")


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    if _rate_limited(request):
        raise HTTPException(status_code=429, detail={"error": {"code": "LOGIN_RATE_LIMITED", "message": "Too many login attempts. Try again later."}})
    _record_attempt(request)
    user = db.query(AppUser).filter(AppUser.id == payload.user_id).first()
    if not user or not user.is_active or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail={"error": {"code": "INVALID_CREDENTIALS", "message": "Invalid user ID or password."}})
    session_token, csrf_token, session = create_session(db, user)
    _set_auth_cookies(response, session_token, csrf_token)
    return {"user": _auth_user(user), "expires_at": session.expires_at}


@router.get("/me")
def me(user: AppUser = Depends(get_current_user)):
    return {"user": _auth_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db), csrf_token: str | None = Header(default=None, alias=CSRF_HEADER)):
    token = request.cookies.get(SESSION_COOKIE)
    session = get_session(db, token)
    if session and not csrf_valid(session, csrf_token):
        raise HTTPException(status_code=403, detail={"error": {"code": "CSRF_REQUIRED", "message": "A valid CSRF token is required."}})
    invalidate_session(db, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}
