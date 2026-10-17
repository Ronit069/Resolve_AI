"""
Tests for real JWT authentication (app/core/security.py +
app/api/endpoints/auth.py + the Authorization: Bearer path in
app/api/deps.py::get_current_user). The pre-existing X-User-Id header
path and its own tests (tests/test_auth_deps.py) are untouched and
still pass -- it's a dev-only fallback now, locked out in production.
"""

import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, get_db
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.models.shared import AppUser, AppUserRole, Merchant
from app.api.deps import get_current_user
from app.api.endpoints.auth import router as auth_router

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_jwt_auth.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app = FastAPI()
app.dependency_overrides[get_db] = override_get_db
app.include_router(auth_router, prefix="/api/v1/auth")


@app.get("/test/whoami")
def whoami(user: AppUser = Depends(get_current_user)):
    return {"user_id": str(user.user_id)}


client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def clean_db():
    db = TestingSessionLocal()
    db.query(AppUser).delete()
    db.query(Merchant).delete()
    db.commit()
    db.close()


def _create_user_with_password(email: str, password: str, active: bool = True) -> uuid.UUID:
    db = TestingSessionLocal()
    merchant = Merchant(external_merchant_id=f"ext_{uuid.uuid4()}", name="Test Merchant", is_active=True)
    db.add(merchant)
    db.commit()
    user = AppUser(
        merchant_id=merchant.merchant_id, email=email, is_active=active,
        role=AppUserRole.APPROVER, password_hash=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    user_id = user.user_id
    db.close()
    return user_id


# --- app/core/security.py unit tests ---

def test_hash_password_and_verify_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False


def test_verify_password_never_raises_on_malformed_hash():
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


def test_access_token_roundtrip():
    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    assert decode_access_token(token) == user_id


def test_decode_rejects_tampered_token():
    from app.core.security import InvalidAccessToken

    token = create_access_token(uuid.uuid4())
    tampered = token[:-4] + ("A" * 4 if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(InvalidAccessToken):
        decode_access_token(tampered)


# --- POST /api/v1/auth/login ---

def test_login_succeeds_with_correct_credentials_and_returns_usable_token():
    user_id = _create_user_with_password("approver@test.com", "s3cret-pw")

    res = client.post("/api/v1/auth/login", json={"email": "approver@test.com", "password": "s3cret-pw"})
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["user_id"] == str(user_id)
    assert body["role"] == "APPROVER"

    whoami_res = client.get("/test/whoami", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert whoami_res.status_code == 200
    assert whoami_res.json()["user_id"] == str(user_id)


def test_login_fails_with_wrong_password():
    _create_user_with_password("user2@test.com", "correct-pw")
    res = client.post("/api/v1/auth/login", json={"email": "user2@test.com", "password": "wrong-pw"})
    assert res.status_code == 401


def test_login_fails_for_unknown_email():
    res = client.post("/api/v1/auth/login", json={"email": "nobody@test.com", "password": "x"})
    assert res.status_code == 401


def test_login_fails_for_inactive_user():
    _create_user_with_password("inactive@test.com", "pw", active=False)
    res = client.post("/api/v1/auth/login", json={"email": "inactive@test.com", "password": "pw"})
    assert res.status_code == 401


def test_login_fails_for_user_with_no_password_set():
    db = TestingSessionLocal()
    merchant = Merchant(external_merchant_id=f"ext_{uuid.uuid4()}", name="M", is_active=True)
    db.add(merchant)
    db.commit()
    user = AppUser(merchant_id=merchant.merchant_id, email="nopw@test.com", is_active=True, role=AppUserRole.APPROVER)
    db.add(user)
    db.commit()
    db.close()

    res = client.post("/api/v1/auth/login", json={"email": "nopw@test.com", "password": "anything"})
    assert res.status_code == 401


# --- Authorization: Bearer path in get_current_user ---

def test_bearer_token_rejected_when_malformed():
    res = client.get("/test/whoami", headers={"Authorization": "NotBearer sometoken"})
    assert res.status_code == 401


def test_bearer_token_rejected_when_invalid():
    res = client.get("/test/whoami", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert res.status_code == 401


def test_bearer_token_for_unknown_user_id_rejected():
    token = create_access_token(uuid.uuid4())
    res = client.get("/test/whoami", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_no_credentials_at_all_rejected():
    res = client.get("/test/whoami")
    assert res.status_code == 401


# --- X-User-Id is a dev-only fallback, hard-disabled in production ---

def test_x_user_id_still_works_in_development(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    user_id = _create_user_with_password("dev-fallback@test.com", "pw")
    res = client.get("/test/whoami", headers={"X-User-Id": str(user_id)})
    assert res.status_code == 200
    assert res.json()["user_id"] == str(user_id)


def test_x_user_id_is_rejected_in_production(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    user_id = _create_user_with_password("prod-blocked@test.com", "pw")
    res = client.get("/test/whoami", headers={"X-User-Id": str(user_id)})
    assert res.status_code == 401
    assert "disabled outside development" in res.json()["detail"]


def test_bearer_token_still_works_in_production(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    user_id = _create_user_with_password("prod-bearer@test.com", "pw")
    token = create_access_token(user_id)
    res = client.get("/test/whoami", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["user_id"] == str(user_id)
