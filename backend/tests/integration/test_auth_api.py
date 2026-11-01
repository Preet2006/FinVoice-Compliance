from app.config import settings
from app.models import AppUser
from app.routers import auth as auth_router
from app.services.auth_service import hash_password


def _set_password(db_session, user_id="u-mgr1", password="Correct horse battery staple"):
    user = db_session.query(AppUser).filter(AppUser.id == user_id).one()
    user.password_hash = hash_password(password)
    db_session.commit()


def test_login_me_and_logout_manage_a_hashed_session(client, db_session):
    _set_password(db_session)
    response = client.post("/api/auth/login", json={"user_id": "u-mgr1", "password": "Correct horse battery staple"})
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "manager"
    assert "password_hash" not in response.json()["user"]
    assert client.get("/api/auth/me").json()["user"]["id"] == "u-mgr1"

    csrf = client.cookies.get("shaha_csrf")
    logged_out = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logged_out.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_invalid_and_inactive_accounts_use_generic_authentication_errors(client, db_session):
    _set_password(db_session)
    bad = client.post("/api/auth/login", json={"user_id": "u-mgr1", "password": "wrong password"})
    assert bad.status_code == 401
    assert bad.json()["error"]["message"] == "Invalid user ID or password."

    user = db_session.query(AppUser).filter(AppUser.id == "u-mgr1").one()
    user.is_active = False
    db_session.commit()
    inactive = client.post("/api/auth/login", json={"user_id": "u-mgr1", "password": "Correct horse battery staple"})
    assert inactive.status_code == 401
    assert inactive.json()["error"]["message"] == "Invalid user ID or password."


def test_cookie_state_changes_require_csrf(client, db_session):
    _set_password(db_session, user_id="u-mgr2")
    assert client.post("/api/auth/login", json={"user_id": "u-mgr2", "password": "Correct horse battery staple"}).status_code == 200
    response = client.post("/api/violations/v-1/review", json={"decision": "confirmed", "comment": "checked"})
    assert response.status_code == 403


def test_forged_identity_headers_do_not_override_authenticated_user(client, db_session, monkeypatch):
    _set_password(db_session)
    monkeypatch.setattr(settings, "allow_legacy_headers", False)
    assert client.post("/api/auth/login", json={"user_id": "u-mgr1", "password": "Correct horse battery staple"}).status_code == 200
    response = client.get("/api/dashboard", headers={"X-Role": "head", "X-User-Id": "u-head"})
    assert response.status_code == 200
    assert response.json()["scope"] == "manager"
    monkeypatch.setattr(settings, "allow_legacy_headers", True)


def test_metadata_requires_a_session(client):
    assert client.get("/api/meta/users").status_code == 401
    assert client.get("/api/meta/offices").status_code == 401


def test_authenticated_user_can_read_metadata(client, db_session):
    _set_password(db_session)
    assert client.post("/api/auth/login", json={"user_id": "u-mgr1", "password": "Correct horse battery staple"}).status_code == 200
    assert client.get("/api/meta/offices").status_code == 200
    assert client.get("/api/meta/users?role=caller").status_code == 200


def test_login_rate_limit_is_enforced(client, db_session, monkeypatch):
    _set_password(db_session)
    monkeypatch.setattr(settings, "auth_login_max_attempts", 2)
    auth_router._attempts.clear()
    for _ in range(2):
        assert client.post("/api/auth/login", json={"user_id": "u-mgr1", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"user_id": "u-mgr1", "password": "wrong"}).status_code == 429
    auth_router._attempts.clear()
