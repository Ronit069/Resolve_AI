import pytest
import uuid
import itertools
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import get_db, Base
from app.api.deps import get_current_merchant
from app.models.shared import Merchant, Case, AppUser, AppUserRole
from app.models.module_a import Dispute
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidencePolicyVersion, EValidationRunStatus
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy
from app.models.module_h import ReviewQueueItem, QueueStatus

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_i_queue_pg"
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


def make_merchant(db: Session, name="Test Merchant I-Queue"):
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


def make_prediction_dependencies(db: Session, case: Case, recommendation="CONTEST", hard_block=False):
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
        recommendation=recommendation,
        hard_block=hard_block,
        idempotency_key=f"pred_{uuid.uuid4()}",
    )
    db.add(prediction)
    db.flush()
    return prediction


def make_queue_case(
    db: Session,
    merchant: Merchant,
    queue_status=QueueStatus.PENDING,
    respond_by=None,
    priority_score=100,
    amount_minor=1000,
    reason_code="fraud",
    dispute_status="open",
    recommendation="CONTEST",
    hard_block=False,
):
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.flush()

    dispute = Dispute(
        case_id=case.case_id,
        external_dispute_id=case.external_dispute_id,
        payment_id=f"pay_{uuid.uuid4()}",
        amount_minor=amount_minor,
        currency="INR",
        reason_code=reason_code,
        status=dispute_status,
        dispute_created_at=datetime.now(timezone.utc),
        respond_by=respond_by or (datetime.now(timezone.utc) + timedelta(days=1)),
    )
    db.add(dispute)
    db.flush()

    prediction = make_prediction_dependencies(db, case, recommendation=recommendation, hard_block=hard_block)

    queue_item = ReviewQueueItem(
        case_id=case.case_id,
        prediction_id=prediction.id,
        priority_score=priority_score,
        queue_status=queue_status,
        respond_by=respond_by or (datetime.now(timezone.utc) + timedelta(days=1)),
    )
    db.add(queue_item)
    db.flush()
    return case, dispute, queue_item, prediction


FIXED_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


# 1. Basic contract
def test_queue_listing_basic_contract(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant, role=AppUserRole.APPROVER)
    case, dispute, qi, pred = make_queue_case(
        db, merchant, amount_minor=5000, reason_code="fraud", dispute_status="open", recommendation="CONTEST"
    )
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get("/api/v1/cases/queue", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    item = body["items"][0]
    assert item["case_id"] == str(case.case_id)
    assert item["queue_item_id"] == str(qi.id)
    assert item["queue_status"] == "PENDING"
    assert item["dispute_amount_minor"] == 5000
    assert item["dispute_currency"] == "INR"
    assert item["dispute_reason_code"] == "fraud"
    assert item["dispute_status"] == "open"
    assert item["recommendation"] == "CONTEST"
    assert item["hard_block"] is False


# 2. Status filter
def test_queue_listing_status_filter(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    make_queue_case(db, merchant, queue_status=QueueStatus.PENDING)
    make_queue_case(db, merchant, queue_status=QueueStatus.DONE)
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get(
        "/api/v1/cases/queue", params={"status": "PENDING"}, headers={"X-User-Id": str(user.user_id)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["items"][0]["queue_status"] == "PENDING"


# 3. Pagination
def test_queue_listing_pagination(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    for i in range(5):
        make_queue_case(db, merchant, respond_by=FIXED_NOW + timedelta(hours=i))
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get(
        "/api/v1/cases/queue", params={"limit": 2, "offset": 0}, headers={"X-User-Id": str(user.user_id)}
    )
    body = response.json()
    assert body["total_count"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0

    response2 = client.get(
        "/api/v1/cases/queue", params={"limit": 2, "offset": 2}, headers={"X-User-Id": str(user.user_id)}
    )
    body2 = response2.json()
    assert len(body2["items"]) == 2
    assert body["items"][0]["case_id"] != body2["items"][0]["case_id"]


# 4. respond_by sort
def test_queue_listing_sort_respond_by(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    case_a, *_ = make_queue_case(db, merchant, respond_by=FIXED_NOW + timedelta(hours=5))
    case_b, *_ = make_queue_case(db, merchant, respond_by=FIXED_NOW + timedelta(hours=1))
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get(
        "/api/v1/cases/queue", params={"sort": "respond_by:asc"}, headers={"X-User-Id": str(user.user_id)}
    )
    items = response.json()["items"]
    assert items[0]["case_id"] == str(case_b.case_id)
    assert items[1]["case_id"] == str(case_a.case_id)


# 5. priority_score sort
def test_queue_listing_sort_priority_score(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    case_low, *_ = make_queue_case(db, merchant, priority_score=10)
    case_high, *_ = make_queue_case(db, merchant, priority_score=90)
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get(
        "/api/v1/cases/queue", params={"sort": "priority_score:desc"}, headers={"X-User-Id": str(user.user_id)}
    )
    items = response.json()["items"]
    assert items[0]["case_id"] == str(case_high.case_id)
    assert items[1]["case_id"] == str(case_low.case_id)


# 6. Deterministic tie-break
def test_queue_listing_deterministic_tie_break(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    same_time = FIXED_NOW + timedelta(hours=3)
    make_queue_case(db, merchant, respond_by=same_time)
    make_queue_case(db, merchant, respond_by=same_time)
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    r1 = client.get("/api/v1/cases/queue", headers={"X-User-Id": str(user.user_id)})
    r2 = client.get("/api/v1/cases/queue", headers={"X-User-Id": str(user.user_id)})
    order1 = [i["case_id"] for i in r1.json()["items"]]
    order2 = [i["case_id"] for i in r2.json()["items"]]
    assert order1 == order2


# 7. Empty result
def test_queue_listing_empty(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get("/api/v1/cases/queue", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total_count"] == 0


# 8. RBAC rejection
def test_queue_listing_rbac_rejects_disallowed_role(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant, role=AppUserRole.SYSTEM_WORKER)
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get("/api/v1/cases/queue", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 403


# 9. Missing X-User-Id
def test_queue_listing_requires_auth_header(client, db):
    merchant = make_merchant(db)
    db.commit()
    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get("/api/v1/cases/queue")
    assert response.status_code == 422


# 10. Tenant isolation
def test_queue_listing_tenant_isolation(client, db):
    merchant_a = make_merchant(db, name="Merchant A Queue")
    merchant_b = make_merchant(db, name="Merchant B Queue")
    user_a = make_user(db, merchant_a)
    make_queue_case(db, merchant_a)
    make_queue_case(db, merchant_b)
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchant_a
    response = client.get("/api/v1/cases/queue", headers={"X-User-Id": str(user_a.user_id)})
    body = response.json()
    assert body["total_count"] == 1


# 11. No writes
def test_queue_listing_causes_no_writes(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant)
    case, dispute, qi, pred = make_queue_case(db, merchant)
    db.commit()

    before_qi = db.query(ReviewQueueItem).count()
    before_case = db.query(Case).count()

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get("/api/v1/cases/queue", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 200

    after_qi = db.query(ReviewQueueItem).count()
    after_case = db.query(Case).count()
    assert after_qi == before_qi
    assert after_case == before_case
