import pytest
import uuid
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import get_db, Base
from app.api.deps import get_current_merchant
from app.models.shared import Merchant, Case, AppUser, AppUserRole
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidencePolicyVersion, EValidationRunStatus
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy
from app.models.module_h import ReviewQueueItem, ReviewAction, QueueStatus, ReviewActionEnum

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_h_22_pg"
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


def make_merchant(db: Session, name="Test Merchant H22"):
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


import itertools
_policy_version_counter = itertools.count(1)


def make_prediction_dependencies(db: Session, case: Case):
    """Builds the minimal chain RiskPrediction requires (mirrors test_module_h_step03.py)."""
    # Each call needs its own (payment_network, reason_code, phase, version) identity
    # to satisfy uq_ev_pol_ver_identity when a test creates multiple queue items.
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


def make_queue_item(
    db: Session,
    merchant: Merchant,
    queue_status=QueueStatus.PENDING,
    created_at=None,
    respond_by=None,
):
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.flush()

    prediction = make_prediction_dependencies(db, case)

    queue_item = ReviewQueueItem(
        case_id=case.case_id,
        prediction_id=prediction.id,
        priority_score=100,
        queue_status=queue_status,
        respond_by=respond_by or (datetime.now(timezone.utc) + timedelta(days=1)),
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(queue_item)
    db.flush()
    return case, queue_item


def make_review_action(db: Session, queue_item: ReviewQueueItem, reviewer: AppUser, action=ReviewActionEnum.APPROVE_CONTEST, created_at=None):
    review_action = ReviewAction(
        queue_item_id=queue_item.id,
        case_id=queue_item.case_id,
        reviewer_id=reviewer.user_id,
        action=action,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(review_action)
    db.flush()
    return review_action


FIXED_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


from app.services.observability.queue_metrics import (
    compute_queue_age_metrics,
    compute_near_deadline_metrics,
    compute_review_turnaround_metrics,
)


# 1. Queue age calculation
def test_queue_age_calculation(db):
    merchant = make_merchant(db)
    make_queue_item(db, merchant, queue_status=QueueStatus.PENDING, created_at=FIXED_NOW - timedelta(hours=2))
    db.commit()

    result = compute_queue_age_metrics(db, merchant.merchant_id, current_time=FIXED_NOW)
    assert result.active_item_count == 1
    assert result.average_age_seconds == pytest.approx(7200, abs=1)
    assert result.min_age_seconds == pytest.approx(7200, abs=1)
    assert result.max_age_seconds == pytest.approx(7200, abs=1)


# 2. Exclusion/handling of DONE items
def test_queue_age_excludes_done_items(db):
    merchant = make_merchant(db)
    make_queue_item(db, merchant, queue_status=QueueStatus.PENDING, created_at=FIXED_NOW - timedelta(hours=1))
    make_queue_item(db, merchant, queue_status=QueueStatus.DONE, created_at=FIXED_NOW - timedelta(hours=10))
    db.commit()

    result = compute_queue_age_metrics(db, merchant.merchant_id, current_time=FIXED_NOW)
    assert result.active_item_count == 1
    assert result.average_age_seconds == pytest.approx(3600, abs=1)


# 3. Near-deadline exactly at 24 hours (inclusive boundary)
def test_near_deadline_exactly_24_hours(db):
    merchant = make_merchant(db)
    make_queue_item(db, merchant, queue_status=QueueStatus.PENDING, respond_by=FIXED_NOW + timedelta(hours=24))
    db.commit()

    result = compute_near_deadline_metrics(db, merchant.merchant_id, current_time=FIXED_NOW)
    assert result.near_deadline_count == 1
    assert result.expired_count == 0


# 4. Near-deadline inside 24 hours
def test_near_deadline_inside_24_hours(db):
    merchant = make_merchant(db)
    make_queue_item(db, merchant, queue_status=QueueStatus.PENDING, respond_by=FIXED_NOW + timedelta(hours=1))
    db.commit()

    result = compute_near_deadline_metrics(db, merchant.merchant_id, current_time=FIXED_NOW)
    assert result.near_deadline_count == 1
    assert result.expired_count == 0


# 5. Expired deadlines reported separately, never as near-deadline
def test_expired_deadline_reported_separately(db):
    merchant = make_merchant(db)
    make_queue_item(db, merchant, queue_status=QueueStatus.PENDING, respond_by=FIXED_NOW - timedelta(hours=1))
    db.commit()

    result = compute_near_deadline_metrics(db, merchant.merchant_id, current_time=FIXED_NOW)
    assert result.expired_count == 1
    assert result.near_deadline_count == 0


# 6. Deadlines beyond 24 hours: counted in neither bucket
def test_deadline_beyond_24_hours_not_counted(db):
    merchant = make_merchant(db)
    make_queue_item(db, merchant, queue_status=QueueStatus.PENDING, respond_by=FIXED_NOW + timedelta(hours=48))
    db.commit()

    result = compute_near_deadline_metrics(db, merchant.merchant_id, current_time=FIXED_NOW)
    assert result.near_deadline_count == 0
    assert result.expired_count == 0


# 7. Review turnaround using the actual finalizing action
def test_review_turnaround_single_action(db):
    merchant = make_merchant(db)
    reviewer = make_user(db, merchant)
    case, qi = make_queue_item(db, merchant, queue_status=QueueStatus.DONE, created_at=FIXED_NOW - timedelta(hours=1))
    make_review_action(db, qi, reviewer, created_at=FIXED_NOW)
    db.commit()

    result = compute_review_turnaround_metrics(db, merchant.merchant_id, current_time=FIXED_NOW)
    assert result.completed_item_count == 1
    assert result.average_turnaround_seconds == pytest.approx(3600, abs=1)


# 8. Multiple ReviewActions for one queue item (dual control): turnaround uses the
# finalizing (max created_at) action, not the first and not an average of both.
def test_review_turnaround_uses_finalizing_action_among_multiple(db):
    merchant = make_merchant(db)
    reviewer1 = make_user(db, merchant)
    reviewer2 = make_user(db, merchant)
    t0 = FIXED_NOW - timedelta(hours=2)
    case, qi = make_queue_item(db, merchant, queue_status=QueueStatus.DONE, created_at=t0)
    make_review_action(db, qi, reviewer1, created_at=t0 + timedelta(seconds=1000))
    make_review_action(db, qi, reviewer2, created_at=t0 + timedelta(seconds=5000))
    db.commit()

    result = compute_review_turnaround_metrics(db, merchant.merchant_id, current_time=FIXED_NOW)
    assert result.completed_item_count == 1
    assert result.average_turnaround_seconds == pytest.approx(5000, abs=1)


# 9. Empty queue: no exceptions, all counts 0, all averages None
def test_empty_queue_metrics(db):
    merchant = make_merchant(db)
    db.commit()

    age = compute_queue_age_metrics(db, merchant.merchant_id, current_time=FIXED_NOW)
    deadline = compute_near_deadline_metrics(db, merchant.merchant_id, current_time=FIXED_NOW)
    turnaround = compute_review_turnaround_metrics(db, merchant.merchant_id, current_time=FIXED_NOW)

    assert age.active_item_count == 0
    assert age.average_age_seconds is None
    assert deadline.near_deadline_count == 0
    assert deadline.expired_count == 0
    assert turnaround.completed_item_count == 0
    assert turnaround.average_turnaround_seconds is None


# 10. Authentication/authorization behavior
def test_endpoint_requires_auth_header(client, db):
    merchant = make_merchant(db)
    db.commit()
    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get("/api/v1/observability/queue-metrics")
    # X-User-Id is a required header on the existing frozen get_current_user
    # dependency, so FastAPI rejects the request at validation time (422)
    # before get_current_user ever runs, rather than 401.
    assert response.status_code == 422


def test_endpoint_rejects_inactive_user(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant, role=AppUserRole.APPROVER, is_active=False)
    db.commit()
    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get("/api/v1/observability/queue-metrics", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 401


def test_endpoint_rejects_disallowed_role(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant, role=AppUserRole.SYSTEM_WORKER)
    db.commit()
    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get("/api/v1/observability/queue-metrics", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 403


@pytest.mark.parametrize("role", [AppUserRole.MERCHANT_ADMIN, AppUserRole.RISK_ANALYST, AppUserRole.APPROVER])
def test_endpoint_allows_authorized_roles(client, db, role):
    merchant = make_merchant(db)
    user = make_user(db, merchant, role=role)
    make_queue_item(db, merchant, queue_status=QueueStatus.PENDING)
    db.commit()
    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get("/api/v1/observability/queue-metrics", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"generated_at", "queue_age", "near_deadline", "review_turnaround"}
    assert body["near_deadline"]["threshold_hours"] == 24
    # No case-level PII exposed anywhere in the response.
    assert "case_id" not in body
    assert "reviewer_id" not in str(body)


# 11. No writes caused by the endpoint
def test_endpoint_causes_no_writes(client, db):
    merchant = make_merchant(db)
    user = make_user(db, merchant, role=AppUserRole.APPROVER)
    case, qi = make_queue_item(db, merchant, queue_status=QueueStatus.PENDING)
    db.commit()

    before_queue_count = db.query(ReviewQueueItem).count()
    before_action_count = db.query(ReviewAction).count()
    before_status = qi.queue_status

    app.dependency_overrides[get_current_merchant] = lambda: merchant
    response = client.get("/api/v1/observability/queue-metrics", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 200

    after_queue_count = db.query(ReviewQueueItem).count()
    after_action_count = db.query(ReviewAction).count()
    db.refresh(qi)

    assert after_queue_count == before_queue_count
    assert after_action_count == before_action_count
    assert qi.queue_status == before_status


# 12. No dependency on H-13/H-19 tables
def test_service_module_does_not_reference_deferred_tables():
    import app.services.observability.queue_metrics as m
    import inspect
    source = inspect.getsource(m)
    for forbidden in ("ExternalActionOutbox", "ContestSubmission", "DisputeOutcome"):
        assert forbidden not in source, f"unexpected reference to deferred-table model '{forbidden}' in queue_metrics.py"


# 13. Tenant isolation
def test_tenant_isolation(client, db):
    merchantA = make_merchant(db, name="Merchant A H22")
    merchantB = make_merchant(db, name="Merchant B H22")
    userA = make_user(db, merchantA, role=AppUserRole.APPROVER)
    make_queue_item(db, merchantA, queue_status=QueueStatus.PENDING, created_at=FIXED_NOW - timedelta(hours=5))
    make_queue_item(db, merchantB, queue_status=QueueStatus.PENDING, created_at=FIXED_NOW - timedelta(hours=999))
    db.commit()

    app.dependency_overrides[get_current_merchant] = lambda: merchantA
    response = client.get("/api/v1/observability/queue-metrics", headers={"X-User-Id": str(userA.user_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["queue_age"]["active_item_count"] == 1
    assert body["queue_age"]["average_age_seconds"] < 999 * 3600
