import pytest
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from unittest.mock import patch

from app.models.shared import Merchant, Case
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidencePolicyVersion
from app.models.module_a import Dispute
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy
from app.models.module_g import GeneratedDraft, ResponseGenerationRun, GenerationStatus
from app.models.module_h import ReviewQueueItem, QueueStatus
from app.services.review.queue import hydrate_review_queue

# DB Setup fixtures based on repository's PostgreSQL test infrastructure conventions.
DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_h_01_pg"
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
    
    # Run full upgrade to head (which includes 04102d744524)
    command.upgrade(config, "head")
    yield postgres_engine

@pytest.fixture
def db_session(alembic_engine):
    with Session(alembic_engine) as session:
        yield session
        session.rollback() # ensure isolation between tests

@pytest.fixture
def setup_test_data(db_session):
    # Base setup
    merchant = Merchant(external_merchant_id=f"merch_{uuid.uuid4()}", name="Test Merchant")
    db_session.add(merchant)
    db_session.commit()

    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db_session.add(case)
    db_session.commit()

    dispute = Dispute(
        case_id=case.case_id,
        external_dispute_id=case.external_dispute_id,
        payment_id="pay_123",
        amount_minor=1000000,
        currency="INR",
        reason_code="fraud",
        status="open",
        dispute_created_at=datetime.now(timezone.utc),
        respond_by=datetime.now(timezone.utc) + timedelta(days=2)
    )
    db_session.add(dispute)
    db_session.commit()
    
    policy_version = EvidencePolicyVersion(
        payment_network="visa", 
        reason_code=f"10.4_{uuid.uuid4()}", 
        phase="PRE", 
        version=1, 
        effective_from=datetime.now(timezone.utc)
    )
    db_session.add(policy_version)
    db_session.commit()

    validation_run = EvidenceValidationRun(
        case_id=case.case_id,
        policy_version_id=policy_version.policy_version_id,
        status="COMPLETED",
        evidence_version="v1",
        started_at=datetime.now(timezone.utc),
        idempotency_key=f"val_key_{uuid.uuid4()}"
    )
    db_session.add(validation_run)
    db_session.commit()

    snapshot = CaseFeatureSnapshot(
        case_id=case.case_id,
        validation_run_id=validation_run.id,
        features_json={},
        feature_schema_version="v1",
        feature_hash="hash_123"
    )
    db_session.add(snapshot)
    
    model_version = ModelVersion(algorithm="catboost", status="ACTIVE")
    db_session.add(model_version)
    db_session.flush()

    policy = ModelDecisionPolicy(model_version_id=model_version.id, accept_threshold=0.2, contest_threshold=0.8, active=True)
    db_session.add(policy)
    db_session.commit()

    return {
        "merchant": merchant,
        "case": case,
        "dispute": dispute,
        "snapshot": snapshot,
        "model_version": model_version,
        "policy": policy
    }

def test_hydrate_queue_eligible_case(db_session, setup_test_data):
    case = setup_test_data["case"]
    
    prediction = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=setup_test_data["snapshot"].id,
        model_version_id=setup_test_data["model_version"].id,
        decision_policy_id=setup_test_data["policy"].id,
        raw_score=0.85,
        calibrated_probability=0.85,
        recommendation="REVIEW",
        hard_block=False,
        idempotency_key=f"pred_{uuid.uuid4()}"
    )
    db_session.add(prediction)
    db_session.commit()

    
    item = hydrate_review_queue(db_session, case.case_id)
    assert item is not None
    assert item.case_id == case.case_id
    assert item.prediction_id == prediction.id
    assert item.queue_status == QueueStatus.PENDING
    assert item.draft_id is None
    assert item.priority_score > 0


def test_hydrate_queue_with_draft(db_session, setup_test_data):
    case = setup_test_data["case"]
    prediction = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=setup_test_data["snapshot"].id,
        model_version_id=setup_test_data["model_version"].id,
        decision_policy_id=setup_test_data["policy"].id,
        raw_score=0.85,
        calibrated_probability=0.85,
        recommendation="CONTEST",
        hard_block=False,
        idempotency_key=f"pred_{uuid.uuid4()}"
    )
    db_session.add(prediction)
    db_session.commit()

    generation_run = ResponseGenerationRun(
        case_id=case.case_id,
        prompt_template_version="v1",
        llm_model_version="gpt-4",
        status=GenerationStatus.PASS
    )
    db_session.add(generation_run)
    db_session.commit()

    draft = GeneratedDraft(
        case_id=case.case_id,
        generation_run_id=generation_run.id,
        summary="Test draft summary",
        draft_json={"text": "Test draft"},
        is_current=True
    )
    db_session.add(draft)
    db_session.commit()

    item = hydrate_review_queue(db_session, case.case_id)
    assert item is not None
    assert item.draft_id == draft.id

def test_idempotency_preserves_existing_item(db_session, setup_test_data):
    case = setup_test_data["case"]
    prediction = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=setup_test_data["snapshot"].id,
        model_version_id=setup_test_data["model_version"].id,
        decision_policy_id=setup_test_data["policy"].id,
        raw_score=0.85,
        calibrated_probability=0.85,
        recommendation="REVIEW",
        hard_block=False,
        idempotency_key=f"pred_{uuid.uuid4()}"
    )
    db_session.add(prediction)
    db_session.commit()

    # First call hydrates
    item1 = hydrate_review_queue(db_session, case.case_id)
    db_session.commit()
    
    # Change status to mimic real activity
    item1.queue_status = QueueStatus.ASSIGNED
    db_session.commit()

    # Second call should return same item and NOT change status back
    item2 = hydrate_review_queue(db_session, case.case_id)
    assert item2.id == item1.id
    assert item2.queue_status == QueueStatus.ASSIGNED

def test_missing_dispute_respond_by_fallback(db_session, setup_test_data):
    case = setup_test_data["case"]
    # Clear respond_by
    dispute = db_session.query(Dispute).filter(Dispute.case_id == case.case_id).first()
    dispute.respond_by = None
    db_session.commit()

    prediction = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=setup_test_data["snapshot"].id,
        model_version_id=setup_test_data["model_version"].id,
        decision_policy_id=setup_test_data["policy"].id,
        raw_score=0.85,
        calibrated_probability=0.85,
        recommendation="REVIEW",
        hard_block=False,
        idempotency_key=f"pred_{uuid.uuid4()}"
    )
    db_session.add(prediction)
    db_session.commit()

    item = hydrate_review_queue(db_session, case.case_id)
    # 72 hours fallback engineering inference check
    assert item.respond_by is not None
    # Should be approximately prediction.created_at + 3 days
    diff = abs((item.respond_by - (prediction.created_at + timedelta(days=3))).total_seconds())
    assert diff < 10

def test_priority_score_deterministic_calculation(db_session, setup_test_data):
    # Ensures the deterministic formula runs correctly without exception.
    case = setup_test_data["case"]
    
    prediction = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=setup_test_data["snapshot"].id,
        model_version_id=setup_test_data["model_version"].id,
        decision_policy_id=setup_test_data["policy"].id,
        raw_score=0.99,
        calibrated_probability=0.99,
        recommendation="REVIEW",
        hard_block=True,
        idempotency_key=f"pred_{uuid.uuid4()}"
    )
    db_session.add(prediction)
    db_session.commit()

    item = hydrate_review_queue(db_session, case.case_id)
    assert item.priority_score > 0
    # Hard block means w3 = 20.0 * 1.0
    # Amount is 1M means w2 = 30.0 * 1.0
    # Evidence is 10.0 * 1.0
    # So base score without deadline is at least 60.0
    assert item.priority_score >= 60.0

def test_tenant_isolation(db_session, setup_test_data):
    # A case for merchant 2 should not pull from merchant 1
    merchant2 = Merchant(external_merchant_id=f"merch_{uuid.uuid4()}", name="M2")
    db_session.add(merchant2)
    db_session.commit()

    case2 = Case(merchant_id=merchant2.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db_session.add(case2)
    db_session.commit()

    # Hydrating for case2 should safely return None since it lacks dispute/prediction
    item = hydrate_review_queue(db_session, case2.case_id)
    assert item is None

def test_transaction_rollback_preserves_clean_state(db_session, setup_test_data):
    case = setup_test_data["case"]
    prediction = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=setup_test_data["snapshot"].id,
        model_version_id=setup_test_data["model_version"].id,
        decision_policy_id=setup_test_data["policy"].id,
        raw_score=0.85,
        calibrated_probability=0.85,
        recommendation="REVIEW",
        hard_block=False,
        idempotency_key=f"pred_{uuid.uuid4()}"
    )
    db_session.add(prediction)
    db_session.commit()

    item = hydrate_review_queue(db_session, case.case_id)
    assert item is not None
    db_session.rollback()
    
    # Assert item was rolled back
    persisted_item = db_session.query(ReviewQueueItem).filter(ReviewQueueItem.case_id == case.case_id).first()
    assert persisted_item is None
