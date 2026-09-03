import pytest
import uuid
import itertools
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import get_db, Base
from app.api.deps import get_current_merchant
from app.models.shared import Merchant, Case, AppUser, AppUserRole, AuditLog
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidencePolicyVersion, EValidationRunStatus
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy
from app.models.module_h import ReviewQueueItem, ReviewAction, QueueStatus, ReviewActionEnum

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_i_audit_pg"
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


def make_merchant(db: Session, name="Test Merchant I-Audit"):
    merchant = Merchant(name=name, external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()
    return merchant


def make_user(db: Session, merchant: Merchant, role=AppUserRole.APPROVER, is_active=True):
    user = AppUser(
        merchant_id=merchant.merchant_id,
        email=f"user_{uuid.uuid4()}@test.com",
        is_active=is_active,
        role=role,
    )
    db.add(user)
    db.flush()
    return user


_policy_version_counter = itertools.count(1)


def make_prediction_dependencies(db: Session, case: Case):
    policy_version = EvidencePolicyVersion(
        payment_network="visa", reason_code="fraud", phase="pre",
        version=next(_policy_version_counter),
        effective_from=datetime.now(timezone.utc),
    )
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
        idempotency_key=f"val_{uuid.uuid4()}",
    )
    db.add(validation_run)
    db.flush()

    snapshot = CaseFeatureSnapshot(
        case_id=case.case_id,
        validation_run_id=validation_run.id,
        feature_schema_version="v1",
        feature_hash="hash",
        features_json={"amount": 1000},
        is_current=True,
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
        idempotency_key=f"pred_{uuid.uuid4()}",
    )
    db.add(prediction)
    db.flush()
    return prediction


def make_case(db: Session, merchant: Merchant):
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.flush()
    return case


def make_queue_item(db: Session, case: Case):
    prediction = make_prediction_dependencies(db, case)
    queue_item = ReviewQueueItem(
        case_id=case.case_id,
        prediction_id=prediction.id,
        priority_score=100,
        queue_status=QueueStatus.PENDING,
        respond_by=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(queue_item)
    db.flush()
    return queue_item


FIXED_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


# 1. Merge of both sources
def test_audit_feed_merges_both_sources(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant, role=AppUserRole.APPROVER)
    reviewer = make_user(db, merchant, role=AppUserRole.APPROVER)
    case = make_case(db, merchant)
    qi = make_queue_item(db, case)

    audit_row = AuditLog(
        case_id=case.case_id, user_id=user.user_id, action="DISPUTE_EVENT_INGESTED",
        details="ingested", created_at=FIXED_NOW - timedelta(hours=2),
    )
    db.add(audit_row)

    review_row = ReviewAction(
        queue_item_id=qi.id, case_id=case.case_id, reviewer_id=reviewer.user_id,
        action=ReviewActionEnum.APPROVE_CONTEST, created_at=FIXED_NOW - timedelta(hours=1),
    )
    db.add(review_row)
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get(f"/api/v1/cases/{case.case_id}/audit-log", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 2
    event_types = {item["event_type"] for item in body["items"]}
    assert event_types == {"AUDIT_LOG", "REVIEW_ACTION"}


# 2. event_type correctness (values + actor mapping)
def test_audit_feed_event_type_correctness(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    reviewer = make_user(db, merchant)
    case = make_case(db, merchant)
    qi = make_queue_item(db, case)

    db.add(AuditLog(case_id=case.case_id, user_id=user.user_id, action="INGESTED", details=None, created_at=FIXED_NOW))
    db.add(ReviewAction(
        queue_item_id=qi.id, case_id=case.case_id, reviewer_id=reviewer.user_id,
        action=ReviewActionEnum.REJECT_RECOMMENDATION, override_reason_code="OTHER", notes="test notes",
        created_at=FIXED_NOW + timedelta(minutes=1),
    ))
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get(f"/api/v1/cases/{case.case_id}/audit-log", headers={"X-User-Id": str(user.user_id)})
    items = response.json()["items"]
    review_event = next(i for i in items if i["event_type"] == "REVIEW_ACTION")
    audit_event = next(i for i in items if i["event_type"] == "AUDIT_LOG")
    assert review_event["actor_user_id"] == str(reviewer.user_id)
    assert review_event["action"] == "REJECT_RECOMMENDATION"
    assert "OTHER" in review_event["details"]
    assert "test notes" in review_event["details"]
    assert audit_event["actor_user_id"] == str(user.user_id)
    assert audit_event["action"] == "INGESTED"


# 3. Deterministic ordering
def test_audit_feed_deterministic_ordering(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    reviewer = make_user(db, merchant)
    case = make_case(db, merchant)
    qi = make_queue_item(db, case)

    db.add(AuditLog(case_id=case.case_id, user_id=user.user_id, action="A1", created_at=FIXED_NOW - timedelta(hours=3)))
    db.add(AuditLog(case_id=case.case_id, user_id=user.user_id, action="A2", created_at=FIXED_NOW - timedelta(hours=1)))
    db.add(ReviewAction(
        queue_item_id=qi.id, case_id=case.case_id, reviewer_id=reviewer.user_id,
        action=ReviewActionEnum.ESCALATE, created_at=FIXED_NOW - timedelta(hours=2),
    ))
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    r1 = client.get(f"/api/v1/cases/{case.case_id}/audit-log", headers={"X-User-Id": str(user.user_id)})
    r2 = client.get(f"/api/v1/cases/{case.case_id}/audit-log", headers={"X-User-Id": str(user.user_id)})
    order1 = [(i["event_type"], i["event_id"]) for i in r1.json()["items"]]
    order2 = [(i["event_type"], i["event_id"]) for i in r2.json()["items"]]
    assert order1 == order2
    # newest first
    timestamps = [i["created_at"] for i in r1.json()["items"]]
    assert timestamps == sorted(timestamps, reverse=True)


# 4. Pagination after merge
def test_audit_feed_pagination_after_merge(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    case = make_case(db, merchant)
    for i in range(5):
        db.add(AuditLog(case_id=case.case_id, user_id=user.user_id, action=f"A{i}", created_at=FIXED_NOW - timedelta(minutes=i)))
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get(
        f"/api/v1/cases/{case.case_id}/audit-log", params={"limit": 2, "offset": 0}, headers={"X-User-Id": str(user.user_id)}
    )
    body = response.json()
    assert body["total_count"] == 5
    assert len(body["items"]) == 2

    response2 = client.get(
        f"/api/v1/cases/{case.case_id}/audit-log", params={"limit": 2, "offset": 4}, headers={"X-User-Id": str(user.user_id)}
    )
    assert len(response2.json()["items"]) == 1


# 5. Empty result
def test_audit_feed_empty(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    case = make_case(db, merchant)
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get(f"/api/v1/cases/{case.case_id}/audit-log", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total_count"] == 0


# 6. Cross-tenant -> 404
def test_audit_feed_cross_tenant_404(client, db):
    merchant_a = make_merchant(db, name="Merchant A Audit")
    merchant_b = make_merchant(db, name="Merchant B Audit")
    user_a = make_user(db, merchant_a)
    case_b = make_case(db, merchant_b)
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant_a
    response = client.get(f"/api/v1/cases/{case_b.case_id}/audit-log", headers={"X-User-Id": str(user_a.user_id)})
    assert response.status_code == 404


# 7. Nonexistent case -> 404
def test_audit_feed_nonexistent_case_404(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get(f"/api/v1/cases/{uuid.uuid4()}/audit-log", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 404


# 8. RBAC rejection
def test_audit_feed_rbac_rejects_disallowed_role(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant, role=AppUserRole.SYSTEM_WORKER)
    case = make_case(db, merchant)
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get(f"/api/v1/cases/{case.case_id}/audit-log", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 403


# 9. No writes
def test_audit_feed_causes_no_writes(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    case = make_case(db, merchant)
    db.add(AuditLog(case_id=case.case_id, user_id=user.user_id, action="A1", created_at=FIXED_NOW))
    db.commit()

    before_audit = db.query(AuditLog).count()
    before_action = db.query(ReviewAction).count()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get(f"/api/v1/cases/{case.case_id}/audit-log", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 200

    after_audit = db.query(AuditLog).count()
    after_action = db.query(ReviewAction).count()
    assert after_audit == before_audit
    assert after_action == before_action
