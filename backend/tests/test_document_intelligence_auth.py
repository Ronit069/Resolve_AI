"""
Document-intelligence tenant-isolation/auth regression tests.

Forensic finding: GET /api/v1/cases/{case_id}/document-intelligence had no
identity/tenant enforcement at all (no header, cross-tenant, and inactive
users all returned 200). The fix reuses the project's existing
app.api.deps.get_current_merchant / get_current_user dependency chain —
the same one audit.py's case-scoped endpoints already use — rather than
inventing a new auth mechanism. These tests exercise that REAL dependency
chain end to end (no dependency_overrides), so a regression in
get_current_user/get_current_merchant would also be caught here.
"""
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.core.database import Base, get_db
from app.models.shared import Merchant, AppUser, AppUserRole, Case
from app.models.module_d import CaseDocumentIntelligenceStatus

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_document_intelligence_auth.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_overrides():
    # Defensive: some other test module (test_module_e.py) installs a
    # module-level (import-time) override of get_current_merchant on this
    # same shared app instance and never clears it — a pre-existing,
    # unrelated test-isolation gap. Clearing here (not just at teardown)
    # keeps these tests hermetic regardless of what ran before them in the
    # same pytest session, without touching that other file.
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


client = TestClient(app)


def make_merchant(db, name="Test Merchant", is_active=True):
    m = Merchant(external_merchant_id=f"ext_{uuid.uuid4()}", name=name, is_active=is_active)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m.merchant_id


def make_user(db, merchant_id, role=AppUserRole.APPROVER, is_active=True):
    u = AppUser(
        merchant_id=merchant_id,
        email=f"user_{uuid.uuid4()}@test.com",
        is_active=is_active,
        role=role,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u.user_id


def make_case(db, merchant_id):
    c = Case(merchant_id=merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="synthetic")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c.case_id


def make_status_row(db, case_id, overall_status="REVIEW_REQUIRED"):
    st = CaseDocumentIntelligenceStatus(
        case_id=case_id,
        overall_status=overall_status,
        total_safe_documents=3,
        processed_documents=3,
        review_required_documents=1,
        ready_for_module_e=False,
    )
    db.add(st)
    db.commit()


def get_di(case_id, headers=None):
    return client.get(f"/api/v1/cases/{case_id}/document-intelligence", headers=headers or {})


# A. No identity -> rejected
def test_no_identity_header_rejected():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        case_id = make_case(db, merchant_id)
        make_status_row(db, case_id)
    finally:
        db.close()

    response = get_di(case_id)  # no X-User-Id header at all
    # X-User-Id is a required header on the existing frozen get_current_user
    # dependency, so FastAPI rejects the request at validation time (422)
    # before get_current_user ever runs — same established convention as
    # test_module_h_step22.py::test_endpoint_requires_auth_header and
    # test_module_i_queue.py::test_queue_listing_requires_auth_header.
    assert response.status_code == 422


# B. Valid active user + same-tenant case -> success, response unchanged
def test_same_tenant_authenticated_user_succeeds():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        user_id = make_user(db, merchant_id)
        case_id = make_case(db, merchant_id)
        make_status_row(db, case_id, overall_status="REVIEW_REQUIRED")
    finally:
        db.close()

    response = get_di(case_id, headers={"X-User-Id": str(user_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["overall_status"] == "REVIEW_REQUIRED"
    assert body["total_safe_documents"] == 3
    assert body["processed_documents"] == 3
    assert body["review_required_documents"] == 1
    assert body["ready_for_module_e"] is False


# C. Valid active user + cross-tenant case -> rejected, even with a fully
# valid case_id belonging to a real, different merchant.
def test_cross_tenant_access_rejected_even_for_valid_case_id():
    db = TestingSessionLocal()
    try:
        merchant_a_id = make_merchant(db, name="Merchant A")
        merchant_b_id = make_merchant(db, name="Merchant B")
        user_a_id = make_user(db, merchant_a_id)
        case_b_id = make_case(db, merchant_b_id)
        make_status_row(db, case_b_id)
    finally:
        db.close()

    response = get_di(case_b_id, headers={"X-User-Id": str(user_a_id)})
    assert response.status_code == 404  # anti-enumeration: indistinguishable from not-found
    assert response.json()["detail"] == "Case not found."


# D. Inactive user -> rejected, no access even to their own tenant's case
def test_inactive_user_rejected():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        user_id = make_user(db, merchant_id, is_active=False)
        case_id = make_case(db, merchant_id)
        make_status_row(db, case_id)
    finally:
        db.close()

    response = get_di(case_id, headers={"X-User-Id": str(user_id)})
    assert response.status_code == 401


# E. Unknown/nonexistent user -> rejected
def test_unknown_user_rejected():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        case_id = make_case(db, merchant_id)
        make_status_row(db, case_id)
    finally:
        db.close()

    response = get_di(case_id, headers={"X-User-Id": str(uuid.uuid4())})
    assert response.status_code == 401


# F. Response/business behavior for an authorized user is unchanged,
# including the PENDING default when no status row exists yet.
def test_pending_default_response_unchanged_for_authorized_user():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        user_id = make_user(db, merchant_id)
        case_id = make_case(db, merchant_id)
        # No CaseDocumentIntelligenceStatus row created.
    finally:
        db.close()

    response = get_di(case_id, headers={"X-User-Id": str(user_id)})
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "overall_status": "PENDING",
        "total_safe_documents": 0,
        "processed_documents": 0,
        "review_required_documents": 0,
        "failed_documents": 0,
        "ready_for_module_e": False,
    }


# No client-controlled merchant selection: no query param / header for a
# client-supplied merchant id can override the server-derived tenant.
def test_client_supplied_merchant_id_is_ignored():
    db = TestingSessionLocal()
    try:
        merchant_a_id = make_merchant(db, name="Merchant A2")
        merchant_b_id = make_merchant(db, name="Merchant B2")
        user_a_id = make_user(db, merchant_a_id)
        case_b_id = make_case(db, merchant_b_id)
        make_status_row(db, case_b_id)
    finally:
        db.close()

    response = client.get(
        f"/api/v1/cases/{case_b_id}/document-intelligence",
        params={"merchant_id": str(merchant_b_id)},
        headers={"X-User-Id": str(user_a_id)},
    )
    assert response.status_code == 404


# Nonexistent case_id behaves the same as cross-tenant (existing convention).
def test_nonexistent_case_rejected():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        user_id = make_user(db, merchant_id)
    finally:
        db.close()

    response = get_di(uuid.uuid4(), headers={"X-User-Id": str(user_id)})
    assert response.status_code == 404
