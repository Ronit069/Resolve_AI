import pytest
import uuid
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, get_db
from app.models.shared import Merchant, AppUser, AppUserRole
from app.api.deps import get_current_user, get_current_merchant, require_role

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_auth_deps.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

# Create a small FastAPI app for testing
app = FastAPI()
app.dependency_overrides[get_db] = override_get_db

@app.get("/test/user")
def route_user_endpoint(user: AppUser = Depends(get_current_user)):
    return {"user_id": str(user.user_id), "merchant_id": str(user.merchant_id)}

@app.get("/test/merchant")
def route_merchant_endpoint(merchant: Merchant = Depends(get_current_merchant)):
    return {"merchant_id": str(merchant.merchant_id)}

@app.get("/test/role")
def route_role_endpoint(user: AppUser = Depends(require_role([AppUserRole.APPROVER]))):
    return {"user_id": str(user.user_id), "role": user.role}

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

def create_merchant_and_user(name: str, role: AppUserRole, active: bool = True):
    db = TestingSessionLocal()
    merchant = Merchant(external_merchant_id=f"ext_{uuid.uuid4()}", name=name, is_active=True)
    db.add(merchant)
    db.commit()
    db.refresh(merchant)
    m_id = str(merchant.merchant_id)
    
    user = AppUser(
        merchant_id=merchant.merchant_id, 
        email=f"{uuid.uuid4()}@test.com", 
        is_active=active, 
        role=role
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    u_id = str(user.user_id)
    
    db.close()
    return m_id, u_id

def test_valid_approver_resolves_actual_merchant():
    m_id, u_id = create_merchant_and_user("Test Merch A", AppUserRole.APPROVER)
    
    res = client.get("/test/merchant", headers={"X-User-Id": u_id})
    assert res.status_code == 200
    assert res.json()["merchant_id"] == m_id

def test_multiple_active_merchants_user_resolves_own_merchant():
    m_id_a, u_id_a = create_merchant_and_user("Merch A", AppUserRole.APPROVER)
    m_id_b, u_id_b = create_merchant_and_user("Merch B", AppUserRole.APPROVER)
    m_id_c, u_id_c = create_merchant_and_user("Merch C", AppUserRole.APPROVER)
    
    # Regardless of DB insertion order, user_b should resolve merch_b
    res = client.get("/test/merchant", headers={"X-User-Id": u_id_b})
    assert res.status_code == 200
    assert res.json()["merchant_id"] == m_id_b
    
    res_c = client.get("/test/merchant", headers={"X-User-Id": u_id_c})
    assert res_c.status_code == 200
    assert res_c.json()["merchant_id"] == m_id_c

def test_unknown_x_user_id_fails():
    res = client.get("/test/user", headers={"X-User-Id": str(uuid.uuid4())})
    assert res.status_code == 401
    assert "User not found or inactive" in res.json()["detail"]

def test_inactive_user_id_fails():
    m_id, u_id = create_merchant_and_user("Test Merch", AppUserRole.APPROVER, active=False)
    res = client.get("/test/user", headers={"X-User-Id": u_id})
    assert res.status_code == 401

def test_existing_allowed_role_behavior():
    _, u_id_approver = create_merchant_and_user("Merch A", AppUserRole.APPROVER)
    _, u_id_analyst = create_merchant_and_user("Merch B", AppUserRole.RISK_ANALYST)
    
    res = client.get("/test/role", headers={"X-User-Id": u_id_approver})
    assert res.status_code == 200
    
    res2 = client.get("/test/role", headers={"X-User-Id": u_id_analyst})
    assert res2.status_code == 403
    assert "User role not authorized" in res2.json()["detail"]

def test_tenant_isolation():
    m_id_a, u_id_a = create_merchant_and_user("Merch A", AppUserRole.APPROVER)
    res = client.get("/test/merchant", headers={"X-User-Id": u_id_a})
    assert res.json()["merchant_id"] == m_id_a
