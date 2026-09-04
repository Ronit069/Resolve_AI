"""
D-02 regression tests — evidence endpoint auth/tenant-isolation.

Forensic finding: POST/GET /api/v1/cases/{case_id}/evidence relied on a
client-supplied `user_id` form/query parameter (not the canonical X-User-Id
header) and, even for that parameter, the service-layer ownership check
never verified `AppUser.is_active` — so an inactive user could both list
AND upload evidence and receive a real document_id, even though the same
user is correctly rejected by the standard authenticated workspace path.

The fix reuses the project's canonical, frozen dependency chain
(app.api.deps.get_current_user / get_current_merchant) at the router
boundary — the same pattern already used by document-intelligence
(c8565f5) and audit.py — instead of a bespoke per-endpoint mechanism.
These tests exercise that REAL dependency chain end to end (no
dependency_overrides), so a regression in get_current_user/get_current_merchant
would also be caught here.
"""
import io
import uuid
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.core.database import Base, get_db
from app.models.shared import Merchant, AppUser, AppUserRole, Case, ProcessingState
from app.models.module_c import EvidenceDocument

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_evidence_endpoints_auth.db"
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
    # Defensive: see test_document_intelligence_auth.py — test_module_e.py
    # installs a module-level (import-time), never-cleared override of
    # get_current_merchant on this same shared app instance. Clearing here
    # (not just at teardown) keeps these tests hermetic regardless of
    # collection order, without touching that unrelated file.
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


def make_case(db, merchant_id, state=ProcessingState.AWAITING_EVIDENCE):
    c = Case(merchant_id=merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="synthetic", processing_state=state)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c.case_id


def make_evidence_doc(db, case_id, merchant_id):
    doc = EvidenceDocument(
        case_id=case_id,
        merchant_id=merchant_id,
        evidence_type="INVOICE",
        object_key=f"test/{uuid.uuid4()}",
        mime_type="application/pdf",
        file_size_bytes=100,
        sha256=f"hash_{uuid.uuid4()}",
    )
    db.add(doc)
    db.commit()
    return doc.document_id


def upload_file(content=b"%PDF-1.4\nD-02 test content"):
    return {"file": ("evidence.pdf", io.BytesIO(content), "application/pdf")}


def do_upload(case_id, headers=None, evidence_type="INVOICE", extra_data=None, content=b"%PDF-1.4\nD-02 test content"):
    data = {"evidence_type": evidence_type}
    if extra_data:
        data.update(extra_data)
    return client.post(
        f"/api/v1/cases/{case_id}/evidence",
        data=data,
        files=upload_file(content),
        headers=headers or {},
    )


def do_list(case_id, headers=None, params=None):
    return client.get(f"/api/v1/cases/{case_id}/evidence", headers=headers or {}, params=params or {})


# ---------------------------------------------------------------------------
# A. No identity header -> rejected (both endpoints)
# ---------------------------------------------------------------------------
def test_upload_no_identity_rejected(mocker):
    mocker.patch("app.services.evidence.storage_client")
    mocker.patch("app.services.evidence.scan_evidence_task.delay")
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        case_id = make_case(db, merchant_id)
    finally:
        db.close()

    response = do_upload(case_id)  # no X-User-Id at all
    assert response.status_code == 422


def test_list_no_identity_rejected():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        case_id = make_case(db, merchant_id)
    finally:
        db.close()

    response = do_list(case_id)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# B. Unknown user -> rejected (both endpoints)
# ---------------------------------------------------------------------------
def test_upload_unknown_user_rejected(mocker):
    mocker.patch("app.services.evidence.storage_client")
    mocker.patch("app.services.evidence.scan_evidence_task.delay")
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        case_id = make_case(db, merchant_id)
    finally:
        db.close()

    response = do_upload(case_id, headers={"X-User-Id": str(uuid.uuid4())})
    assert response.status_code == 401


def test_list_unknown_user_rejected():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        case_id = make_case(db, merchant_id)
    finally:
        db.close()

    response = do_list(case_id, headers={"X-User-Id": str(uuid.uuid4())})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# C. Inactive user -> rejected (both endpoints) — the exact forensic finding
# ---------------------------------------------------------------------------
def test_upload_inactive_user_rejected(mocker):
    mocker.patch("app.services.evidence.storage_client")
    mocker.patch("app.services.evidence.scan_evidence_task.delay")
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        user_id = make_user(db, merchant_id, is_active=False)
        case_id = make_case(db, merchant_id)
    finally:
        db.close()

    response = do_upload(case_id, headers={"X-User-Id": str(user_id)})
    assert response.status_code == 401


def test_list_inactive_user_rejected():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        user_id = make_user(db, merchant_id, is_active=False)
        case_id = make_case(db, merchant_id)
    finally:
        db.close()

    response = do_list(case_id, headers={"X-User-Id": str(user_id)})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# D. Active same-tenant user -> success (both endpoints)
# ---------------------------------------------------------------------------
def test_upload_same_tenant_active_user_succeeds(mocker):
    mock_storage = mocker.patch("app.services.evidence.storage_client")
    mock_storage.upload_file.return_value = True
    mocker.patch("app.services.evidence.scan_evidence_task.delay")
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        user_id = make_user(db, merchant_id)
        case_id = make_case(db, merchant_id)
    finally:
        db.close()

    response = do_upload(case_id, headers={"X-User-Id": str(user_id)})
    assert response.status_code == 202
    body = response.json()
    assert body["document_id"]


def test_list_same_tenant_active_user_succeeds():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        user_id = make_user(db, merchant_id)
        case_id = make_case(db, merchant_id)
    finally:
        db.close()

    response = do_list(case_id, headers={"X-User-Id": str(user_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == str(case_id)
    assert body["evidence"] == []


# ---------------------------------------------------------------------------
# E. Active cross-tenant user + valid case_id -> rejected (both endpoints)
# ---------------------------------------------------------------------------
def test_upload_cross_tenant_rejected_even_for_valid_case_id(mocker):
    mocker.patch("app.services.evidence.storage_client")
    mocker.patch("app.services.evidence.scan_evidence_task.delay")
    db = TestingSessionLocal()
    try:
        merchant_a_id = make_merchant(db, name="Merchant A")
        merchant_b_id = make_merchant(db, name="Merchant B")
        user_a_id = make_user(db, merchant_a_id)
        case_b_id = make_case(db, merchant_b_id)
    finally:
        db.close()

    response = do_upload(case_b_id, headers={"X-User-Id": str(user_a_id)})
    assert response.status_code == 404  # anti-enumeration convention
    assert response.json()["detail"] == "Case not found."


def test_list_cross_tenant_rejected_even_for_valid_case_id():
    db = TestingSessionLocal()
    try:
        merchant_a_id = make_merchant(db, name="Merchant A2")
        merchant_b_id = make_merchant(db, name="Merchant B2")
        user_a_id = make_user(db, merchant_a_id)
        case_b_id = make_case(db, merchant_b_id)
    finally:
        db.close()

    response = do_list(case_b_id, headers={"X-User-Id": str(user_a_id)})
    assert response.status_code == 404
    assert response.json()["detail"] == "Case not found."


# ---------------------------------------------------------------------------
# F. Inactive/unknown/cross-tenant upload does NOT create an EvidenceDocument
# ---------------------------------------------------------------------------
def test_unauthorized_uploads_create_no_evidence_rows(mocker):
    mocker.patch("app.services.evidence.storage_client")
    mocker.patch("app.services.evidence.scan_evidence_task.delay")
    db = TestingSessionLocal()
    try:
        merchant_a_id = make_merchant(db, name="Merchant A3")
        merchant_b_id = make_merchant(db, name="Merchant B3")
        inactive_user_id = make_user(db, merchant_a_id, is_active=False)
        cross_tenant_user_id = make_user(db, merchant_b_id)
        case_id = make_case(db, merchant_a_id)
    finally:
        db.close()

    r_no_identity = do_upload(case_id)
    r_unknown = do_upload(case_id, headers={"X-User-Id": str(uuid.uuid4())})
    r_inactive = do_upload(case_id, headers={"X-User-Id": str(inactive_user_id)})
    r_cross_tenant = do_upload(case_id, headers={"X-User-Id": str(cross_tenant_user_id)})

    assert r_no_identity.status_code == 422
    assert r_unknown.status_code == 401
    assert r_inactive.status_code == 401
    assert r_cross_tenant.status_code == 404

    db = TestingSessionLocal()
    try:
        assert db.query(EvidenceDocument).filter(EvidenceDocument.case_id == case_id).count() == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# G. Unauthorized upload does not reach storage at all (no artifact left
# behind) — proven by asserting the storage client is never invoked, which
# is a stronger, more reliable guarantee than inspecting a mock filesystem.
# ---------------------------------------------------------------------------
def test_unauthorized_uploads_never_touch_storage(mocker):
    mock_storage = mocker.patch("app.services.evidence.storage_client")
    mocker.patch("app.services.evidence.scan_evidence_task.delay")
    db = TestingSessionLocal()
    try:
        merchant_a_id = make_merchant(db, name="Merchant A4")
        merchant_b_id = make_merchant(db, name="Merchant B4")
        inactive_user_id = make_user(db, merchant_a_id, is_active=False)
        cross_tenant_user_id = make_user(db, merchant_b_id)
        case_id = make_case(db, merchant_a_id)
    finally:
        db.close()

    do_upload(case_id)
    do_upload(case_id, headers={"X-User-Id": str(uuid.uuid4())})
    do_upload(case_id, headers={"X-User-Id": str(inactive_user_id)})
    do_upload(case_id, headers={"X-User-Id": str(cross_tenant_user_id)})

    mock_storage.upload_file.assert_not_called()


# ---------------------------------------------------------------------------
# H. Authorized upload still succeeds and returns the expected response
# ---------------------------------------------------------------------------
def test_authorized_upload_returns_expected_document_id(mocker):
    mock_storage = mocker.patch("app.services.evidence.storage_client")
    mock_storage.upload_file.return_value = True
    mock_scan = mocker.patch("app.services.evidence.scan_evidence_task.delay")
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        user_id = make_user(db, merchant_id)
        case_id = make_case(db, merchant_id)
    finally:
        db.close()

    response = do_upload(case_id, headers={"X-User-Id": str(user_id)})
    assert response.status_code == 202
    body = response.json()
    assert body["processing_status"] == "QUARANTINED"
    mock_scan.assert_called_once()

    db = TestingSessionLocal()
    try:
        doc = db.query(EvidenceDocument).filter(EvidenceDocument.document_id == uuid.UUID(body["document_id"])).first()
        assert doc is not None
        assert doc.case_id == case_id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# I. Authorized list returns expected evidence
# ---------------------------------------------------------------------------
def test_authorized_list_returns_expected_evidence():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        user_id = make_user(db, merchant_id)
        case_id = make_case(db, merchant_id)
        make_evidence_doc(db, case_id, merchant_id)
    finally:
        db.close()

    response = do_list(case_id, headers={"X-User-Id": str(user_id)})
    assert response.status_code == 200
    body = response.json()
    assert len(body["evidence"]) == 1


# ---------------------------------------------------------------------------
# J. Cross-tenant list does not return evidence
# ---------------------------------------------------------------------------
def test_cross_tenant_list_returns_no_evidence():
    db = TestingSessionLocal()
    try:
        merchant_a_id = make_merchant(db, name="Merchant A5")
        merchant_b_id = make_merchant(db, name="Merchant B5")
        user_a_id = make_user(db, merchant_a_id)
        case_b_id = make_case(db, merchant_b_id)
        make_evidence_doc(db, case_b_id, merchant_b_id)
    finally:
        db.close()

    response = do_list(case_b_id, headers={"X-User-Id": str(user_a_id)})
    assert response.status_code == 404
    assert "evidence" not in response.json()


# ---------------------------------------------------------------------------
# K. Response shape unchanged for authorized users
# ---------------------------------------------------------------------------
def test_list_response_shape_unchanged():
    db = TestingSessionLocal()
    try:
        merchant_id = make_merchant(db)
        user_id = make_user(db, merchant_id)
        case_id = make_case(db, merchant_id)
    finally:
        db.close()

    response = do_list(case_id, headers={"X-User-Id": str(user_id)})
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"case_id", "evidence", "evidence_summary", "processing_state"}
    assert set(body["evidence_summary"].keys()) == {
        "required_count", "available_required_count", "missing_required", "coverage_ratio",
    }


# ---------------------------------------------------------------------------
# Client-controlled merchant selection cannot bypass authorization: neither
# endpoint accepts a merchant identifier at all, but prove that a supplied
# one (as an extra form field / query param) is silently ignored, not used
# to widen access.
# ---------------------------------------------------------------------------
def test_client_supplied_merchant_id_cannot_bypass_upload_authorization(mocker):
    mocker.patch("app.services.evidence.storage_client")
    mocker.patch("app.services.evidence.scan_evidence_task.delay")
    db = TestingSessionLocal()
    try:
        merchant_a_id = make_merchant(db, name="Merchant A6")
        merchant_b_id = make_merchant(db, name="Merchant B6")
        user_a_id = make_user(db, merchant_a_id)
        case_b_id = make_case(db, merchant_b_id)
    finally:
        db.close()

    response = do_upload(
        case_b_id,
        headers={"X-User-Id": str(user_a_id)},
        extra_data={"merchant_id": str(merchant_b_id)},
    )
    assert response.status_code == 404


def test_client_supplied_merchant_id_cannot_bypass_list_authorization():
    db = TestingSessionLocal()
    try:
        merchant_a_id = make_merchant(db, name="Merchant A7")
        merchant_b_id = make_merchant(db, name="Merchant B7")
        user_a_id = make_user(db, merchant_a_id)
        case_b_id = make_case(db, merchant_b_id)
    finally:
        db.close()

    response = do_list(
        case_b_id,
        headers={"X-User-Id": str(user_a_id)},
        params={"merchant_id": str(merchant_b_id)},
    )
    assert response.status_code == 404
