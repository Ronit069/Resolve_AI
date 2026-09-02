import pytest
import uuid
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import get_db, Base
from app.models.shared import Merchant, Case, AppUser, AppUserRole
from app.models.module_h import ReviewActionEnum, ReviewQueueItem, QueueStatus

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_h_04_pg"
TEST_DB_URL = f"postgresql://resolve_user:resolve_password@127.0.0.1:5433/{TEST_DB_NAME}"

@pytest.fixture(scope="module")
def postgres_engine():
    import sqlalchemy as sa
    try:
        engine_default = sa.create_engine(DB_URL, isolation_level="AUTOCOMMIT")
        with engine_default.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
            conn.execute(sa.text(f"CREATE DATABASE {TEST_DB_NAME}"))
    except Exception as e:
        pytest.skip(f"PostgreSQL not available: {e}")

    engine_test = sa.create_engine(TEST_DB_URL)
    yield engine_test
    
    engine_test.dispose()
    try:
        with engine_default.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
    except Exception:
        pass
    engine_default.dispose()

@pytest.fixture(scope="module")
def alembic_engine(postgres_engine):
    from alembic.config import Config
    from alembic import command
    import os
    
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ini_path = os.path.join(base_dir, "alembic.ini")
    
    config = Config(ini_path)
    config.set_main_option("sqlalchemy.url", TEST_DB_URL)
    config.set_main_option("script_location", os.path.join(base_dir, "alembic"))
    
    command.upgrade(config, "head")
    yield postgres_engine

@pytest.fixture
def db(alembic_engine):
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=alembic_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        for table in reversed(Base.metadata.sorted_tables):
            try:
                with alembic_engine.connect() as conn:
                    conn.execute(table.delete())
                    conn.commit()
            except Exception:
                pass

@pytest.fixture
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()

from app.models.module_a import Dispute
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidencePolicyVersion, EValidationRunStatus
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy
from app.models.module_g import GeneratedDraft, GenerationStatus
from datetime import timedelta

def setup_rbac_data(db: Session):
    merchant = Merchant(name="Test Merchant H04", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    
    merchant2 = Merchant(name="Other Merchant", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant2)
    db.flush()
    
    approver = AppUser(merchant_id=merchant.merchant_id, email=f"app_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.APPROVER)
    risk_analyst = AppUser(merchant_id=merchant.merchant_id, email=f"risk_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.RISK_ANALYST)
    merchant_admin = AppUser(merchant_id=merchant.merchant_id, email=f"admin_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.MERCHANT_ADMIN)
    model_maint = AppUser(merchant_id=merchant.merchant_id, email=f"mod_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.MODEL_MAINTAINER)
    inactive_approver = AppUser(merchant_id=merchant.merchant_id, email=f"inact_{uuid.uuid4()}@test.com", is_active=False, role=AppUserRole.APPROVER)
    other_approver = AppUser(merchant_id=merchant2.merchant_id, email=f"other_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.APPROVER)
    
    db.add_all([approver, risk_analyst, merchant_admin, model_maint, inactive_approver, other_approver])
    db.flush()
    
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.flush()

    dispute = Dispute(case_id=case.case_id, external_dispute_id=case.external_dispute_id, payment_id="pay_1", amount_minor=1000, currency="INR", reason_code="fraud", status="open", dispute_created_at=datetime.now(timezone.utc), respond_by=datetime.now(timezone.utc) + timedelta(days=1))
    db.add(dispute)
    
    policy_version = EvidencePolicyVersion(payment_network="visa", reason_code="fraud", phase="pre", version=1, effective_from=datetime.now(timezone.utc))
    db.add(policy_version)
    
    model_version = ModelVersion(algorithm="lgbm", status="active")
    db.add(model_version)
    db.flush()
    
    decision_policy = ModelDecisionPolicy(model_version_id=model_version.id)
    db.add(decision_policy)
    db.flush()
    
    validation_run = EvidenceValidationRun(
        case_id=case.case_id,
        evidence_version="v1",
        policy_version_id=policy_version.policy_version_id,
        status=EValidationRunStatus.COMPLETED,
        started_at=datetime.now(timezone.utc),
        idempotency_key=f"val_{uuid.uuid4()}"
    )
    db.add(validation_run)
    db.flush()
    
    snapshot = CaseFeatureSnapshot(
        case_id=case.case_id,
        validation_run_id=validation_run.id,
        feature_schema_version="v1",
        feature_hash="hash",
        features_json={"amount": 1000},
        is_current=True
    )
    db.add(snapshot)
    db.flush()
    
    prediction = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=snapshot.id,
        model_version_id=model_version.id,
        decision_policy_id=decision_policy.id,
        raw_score=0.9,
        calibrated_probability=0.9,
        recommendation="CONTEST",
        hard_block=False,
        idempotency_key=f"pred_{uuid.uuid4()}"
    )
    db.add(prediction)
    db.flush()
    
    queue_item = ReviewQueueItem(
        case_id=case.case_id,
        prediction_id=prediction.id,
        queue_status=QueueStatus.PENDING,
        priority_score=100,
        respond_by=datetime.now(timezone.utc) + timedelta(days=2)
    )
    db.add(queue_item)
    db.commit()
    
    return merchant, merchant2, case, approver, risk_analyst, merchant_admin, model_maint, inactive_approver, other_approver

def test_approver_can_invoke_action(client: TestClient, db: Session):
    merchant, _, case, approver, *_ = setup_rbac_data(db)
    
    # Missing required fields for H-18 validation to ensure we passed RBAC
    payload = {
        "action": ReviewActionEnum.APPROVE_CONTEST.value,
        "reason_code": None,
        "notes": None
    }
    
    response = client.post(
        f"/api/v1/cases/{case.case_id}/review-action",
        json=payload,
        headers={"X-User-Id": str(approver.user_id)}
    )
    
    # 400 Bad Request indicates it reached business validation (H-18) and passed RBAC
    # (Since missing reason_code for ML contradiction logic will fail)
    assert response.status_code != 403, "Approver should not receive 403 Forbidden"
    assert response.status_code in [201, 400, 422], f"Unexpected status: {response.status_code}"

@pytest.mark.parametrize("role_index", [4, 5, 6]) # risk_analyst, merchant_admin, model_maint
def test_non_approver_rejected(client: TestClient, db: Session, role_index: int):
    data = setup_rbac_data(db)
    case = data[2]
    user = data[role_index]
    
    payload = {
        "action": ReviewActionEnum.APPROVE_CONTEST.value
    }
    
    response = client.post(
        f"/api/v1/cases/{case.case_id}/review-action",
        json=payload,
        headers={"X-User-Id": str(user.user_id)}
    )
    
    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"].lower()

def test_inactive_approver_rejected(client: TestClient, db: Session):
    merchant, _, case, approver, _, _, _, inactive_approver, _ = setup_rbac_data(db)
    
    payload = {
        "action": ReviewActionEnum.APPROVE_CONTEST.value
    }
    
    response = client.post(
        f"/api/v1/cases/{case.case_id}/review-action",
        json=payload,
        headers={"X-User-Id": str(inactive_approver.user_id)}
    )
    
    # existing current-user validation returns 401 for inactive
    assert response.status_code == 401
    assert "inactive" in response.json()["detail"].lower()

def test_tenant_isolation_preserved(client: TestClient, db: Session):
    merchant, merchant2, case, approver, _, _, _, _, other_approver = setup_rbac_data(db)
    
    payload = {
        "action": ReviewActionEnum.APPROVE_CONTEST.value
    }
    
    response = client.post(
        f"/api/v1/cases/{case.case_id}/review-action",
        json=payload,
        headers={"X-User-Id": str(other_approver.user_id)}
    )
    
    # 404 Case not found or 403 Forbidden from get_current_user logic (User does not belong to merchant)
    assert response.status_code in [403, 404]
