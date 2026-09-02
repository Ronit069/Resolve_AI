import inspect
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.shared import Merchant, Case, AppUser
from app.models.module_a import Dispute
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidencePolicyVersion, EValidationRunStatus
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy
from app.models.module_h import ReviewQueueItem, ReviewAction, QueueStatus, ReviewActionEnum
from app.services.external_action.contest_submission_action_gate import (
    determine_contest_submission_action,
)

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_h_10_pg"
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


def setup_case(db: Session):
    merchant = Merchant(name="Test Merchant H10", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()

    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.flush()

    dispute = Dispute(
        case_id=case.case_id, external_dispute_id=case.external_dispute_id, payment_id="pay_1",
        amount_minor=1000, currency="INR", reason_code="fraud", status="open",
        dispute_created_at=datetime.now(timezone.utc), respond_by=datetime.now(timezone.utc) + timedelta(days=1)
    )
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
    db.commit()

    return merchant, case, model_version, decision_policy, validation_run


def make_prediction(db: Session, case: Case, model_version: ModelVersion, decision_policy: ModelDecisionPolicy, validation_run: EvidenceValidationRun):
    snapshot = CaseFeatureSnapshot(
        case_id=case.case_id,
        validation_run_id=validation_run.id,
        feature_schema_version="v1",
        feature_hash=f"hash_{uuid.uuid4()}",
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
    db.commit()
    return prediction


def make_queue_item(db: Session, case: Case, prediction: RiskPrediction, queue_status=QueueStatus.PENDING, pending_review_action_id=None, created_at=None):
    queue_item = ReviewQueueItem(
        case_id=case.case_id,
        prediction_id=prediction.id,
        priority_score=100,
        queue_status=queue_status,
        pending_review_action_id=pending_review_action_id,
        respond_by=datetime.now(timezone.utc) + timedelta(days=1),
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(queue_item)
    db.commit()
    return queue_item


def make_reviewer(db: Session, merchant: Merchant):
    reviewer = AppUser(merchant_id=merchant.merchant_id, email=f"reviewer_{uuid.uuid4()}@test.com", is_active=True)
    db.add(reviewer)
    db.commit()
    return reviewer


def make_review_action(db: Session, queue_item: ReviewQueueItem, case: Case, action: ReviewActionEnum, merchant: Merchant, reviewer_id=None, created_at=None):
    reviewer_id = reviewer_id or make_reviewer(db, merchant).user_id
    review_action = ReviewAction(
        queue_item_id=queue_item.id,
        case_id=case.case_id,
        reviewer_id=reviewer_id,
        action=action,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(review_action)
    db.commit()
    return review_action


FIXED_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


# 1. No queue item for the case => draft, not eligible.
def test_no_queue_item_resolves_to_draft(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.action == "draft"
    assert result.queue_item_id is None


# 2. Queue item PENDING => draft.
def test_pending_queue_item_resolves_to_draft(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    make_queue_item(db, case, prediction, queue_status=QueueStatus.PENDING)

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.action == "draft"
    assert result.queue_status == "PENDING"


# 3. Queue item ASSIGNED => draft.
def test_assigned_queue_item_resolves_to_draft(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    make_queue_item(db, case, prediction, queue_status=QueueStatus.ASSIGNED)

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.action == "draft"


# 4. DONE + finalizing APPROVE_CONTEST (plain H-03 path) => submit.
def test_done_approve_contest_resolves_to_submit(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    qi = make_queue_item(db, case, prediction, queue_status=QueueStatus.DONE)
    action = make_review_action(db, qi, case, ReviewActionEnum.APPROVE_CONTEST, merchant)

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is True
    assert result.action == "submit"
    assert result.finalizing_review_action_id == action.id
    assert result.finalizing_review_action == "APPROVE_CONTEST"


# 5. DONE + finalizing APPROVE_ACCEPT => draft.
def test_done_approve_accept_resolves_to_draft(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    qi = make_queue_item(db, case, prediction, queue_status=QueueStatus.DONE)
    make_review_action(db, qi, case, ReviewActionEnum.APPROVE_ACCEPT, merchant)

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.action == "draft"
    assert result.finalizing_review_action == "APPROVE_ACCEPT"


# 6. DONE + finalizing REJECT_RECOMMENDATION => draft.
def test_done_reject_recommendation_resolves_to_draft(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    qi = make_queue_item(db, case, prediction, queue_status=QueueStatus.DONE)
    make_review_action(db, qi, case, ReviewActionEnum.REJECT_RECOMMENDATION, merchant)

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.action == "draft"


# 7. DONE + finalizing REQUEST_MORE_EVIDENCE => draft.
def test_done_request_more_evidence_resolves_to_draft(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    qi = make_queue_item(db, case, prediction, queue_status=QueueStatus.DONE)
    make_review_action(db, qi, case, ReviewActionEnum.REQUEST_MORE_EVIDENCE, merchant)

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.action == "draft"


# 8. DONE + finalizing EDIT_DRAFT => draft.
def test_done_edit_draft_resolves_to_draft(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    qi = make_queue_item(db, case, prediction, queue_status=QueueStatus.DONE)
    make_review_action(db, qi, case, ReviewActionEnum.EDIT_DRAFT, merchant)

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.action == "draft"


# 9. DONE + finalizing ESCALATE (single-action, non-dual-control) => draft.
def test_done_escalate_resolves_to_draft(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    qi = make_queue_item(db, case, prediction, queue_status=QueueStatus.DONE)
    make_review_action(db, qi, case, ReviewActionEnum.ESCALATE, merchant)

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.action == "draft"


# 10. PENDING_SECOND_APPROVAL (H-05 first approval only) => draft, never eligible.
def test_pending_second_approval_never_eligible(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    qi = make_queue_item(db, case, prediction, queue_status=QueueStatus.PENDING)
    first_action = make_review_action(db, qi, case, ReviewActionEnum.APPROVE_CONTEST, merchant)
    qi.queue_status = QueueStatus.PENDING_SECOND_APPROVAL
    qi.pending_review_action_id = first_action.id
    db.commit()

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.action == "draft"
    assert "second approval" in result.reason.lower()


# 11. H-05 dual control finalized: first APPROVE_CONTEST (pending) + second
# APPROVE_CONTEST (finalizes) => submit; the SECOND action is what's identified.
def test_dual_control_finalized_by_second_approval_resolves_to_submit(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    qi = make_queue_item(db, case, prediction, queue_status=QueueStatus.PENDING)
    first_action = make_review_action(db, qi, case, ReviewActionEnum.APPROVE_CONTEST, merchant, created_at=FIXED_NOW - timedelta(minutes=10))
    qi.queue_status = QueueStatus.PENDING_SECOND_APPROVAL
    qi.pending_review_action_id = first_action.id
    db.commit()

    second_action = make_review_action(db, qi, case, ReviewActionEnum.APPROVE_CONTEST, merchant, created_at=FIXED_NOW - timedelta(minutes=1))
    qi.queue_status = QueueStatus.DONE
    qi.pending_review_action_id = None
    db.commit()

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is True
    assert result.action == "submit"
    assert result.finalizing_review_action_id == second_action.id
    assert result.finalizing_review_action_id != first_action.id


# 12. H-05 dual control cancelled: first APPROVE_CONTEST (pending) + second ESCALATE => draft.
def test_dual_control_cancelled_by_escalate_resolves_to_draft(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    qi = make_queue_item(db, case, prediction, queue_status=QueueStatus.PENDING)
    first_action = make_review_action(db, qi, case, ReviewActionEnum.APPROVE_CONTEST, merchant, created_at=FIXED_NOW - timedelta(minutes=10))
    qi.queue_status = QueueStatus.PENDING_SECOND_APPROVAL
    qi.pending_review_action_id = first_action.id
    db.commit()

    escalate_action = make_review_action(db, qi, case, ReviewActionEnum.ESCALATE, merchant, created_at=FIXED_NOW - timedelta(minutes=1))
    qi.queue_status = QueueStatus.DONE
    qi.pending_review_action_id = None
    db.commit()

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.action == "draft"
    assert result.finalizing_review_action_id == escalate_action.id


# 13. Multiple queue items over time: the most recent one's outcome governs.
def test_most_recent_queue_item_governs(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction1 = make_prediction(db, case, model_version, decision_policy, validation_run)
    old_qi = make_queue_item(db, case, prediction1, queue_status=QueueStatus.DONE, created_at=FIXED_NOW - timedelta(days=5))
    make_review_action(db, old_qi, case, ReviewActionEnum.APPROVE_CONTEST, merchant, created_at=FIXED_NOW - timedelta(days=5))

    # case_feature_snapshots has a UNIQUE constraint on validation_run_id alone,
    # so this second prediction needs its own validation run.
    validation_run2 = EvidenceValidationRun(
        case_id=case.case_id,
        evidence_version="v2",
        policy_version_id=validation_run.policy_version_id,
        status=EValidationRunStatus.COMPLETED,
        started_at=datetime.now(timezone.utc),
        idempotency_key=f"val_{uuid.uuid4()}"
    )
    db.add(validation_run2)
    db.commit()

    prediction2 = make_prediction(db, case, model_version, decision_policy, validation_run2)
    new_qi = make_queue_item(db, case, prediction2, queue_status=QueueStatus.DONE, created_at=FIXED_NOW - timedelta(hours=1))
    make_review_action(db, new_qi, case, ReviewActionEnum.REJECT_RECOMMENDATION, merchant, created_at=FIXED_NOW - timedelta(hours=1))

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.queue_item_id == new_qi.id
    assert result.submit_eligible is False
    assert result.action == "draft"


# 14. Fail-closed defensive case: DONE with zero ReviewAction rows.
def test_done_with_no_review_action_fails_closed(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    make_queue_item(db, case, prediction, queue_status=QueueStatus.DONE)

    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.action == "draft"
    assert "no review action found" in result.reason.lower()


# 15. Fresh DB read is used rather than a stale previously-loaded object.
def test_fresh_read_bypasses_stale_identity_map(db, alembic_engine):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    qi = make_queue_item(db, case, prediction, queue_status=QueueStatus.DONE)
    make_review_action(db, qi, case, ReviewActionEnum.APPROVE_CONTEST, merchant)

    # Load into this session's identity map first, as ordinary app code might.
    loaded = db.query(ReviewQueueItem).filter(ReviewQueueItem.id == qi.id).first()
    assert loaded.queue_status == QueueStatus.DONE

    # Mutate through a completely separate connection/transaction, bypassing
    # this session's identity map entirely (mirrors a concurrent writer).
    import sqlalchemy as sa
    with alembic_engine.connect() as conn:
        conn.execute(
            sa.text("UPDATE review_queue_items SET queue_status = 'PENDING_SECOND_APPROVAL' WHERE id = :id"),
            {"id": str(qi.id)},
        )
        conn.commit()

    # The stale in-memory object still (incorrectly) looks unchanged...
    assert loaded.queue_status == QueueStatus.DONE

    # ...but the gate must see the fresh value, not the stale cached one.
    result = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert result.submit_eligible is False
    assert result.queue_status == "PENDING_SECOND_APPROVAL"


# 16. Signature makes caller-supplied approval/status structurally impossible.
def test_signature_accepts_no_status_or_outcome_override():
    sig = inspect.signature(determine_contest_submission_action)
    param_names = set(sig.parameters.keys())
    assert param_names == {"db", "case_id", "current_time"}
    assert "queue_status" not in param_names
    assert "review_outcome" not in param_names
    assert "approved" not in param_names


# 17. No external-action capability in the module.
def test_no_external_action_capability_in_module():
    import app.services.external_action.contest_submission_action_gate as gate_module
    source = inspect.getsource(gate_module).lower()
    for forbidden in ("import requests", "import httpx", "import urllib", ".get(\"http", ".post(\"http"):
        assert forbidden not in source, f"unexpected outbound-call marker '{forbidden}' found in contest_submission_action_gate.py"


# 18. Deterministic behavior: identical DB state + identical current_time => identical result.
def test_deterministic_behavior(db):
    merchant, case, model_version, decision_policy, validation_run = setup_case(db)
    prediction = make_prediction(db, case, model_version, decision_policy, validation_run)
    qi = make_queue_item(db, case, prediction, queue_status=QueueStatus.DONE)
    make_review_action(db, qi, case, ReviewActionEnum.APPROVE_CONTEST, merchant)

    r1 = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    r2 = determine_contest_submission_action(db, case.case_id, current_time=FIXED_NOW)
    assert r1.submit_eligible == r2.submit_eligible == True
    assert r1.action == r2.action == "submit"
    assert r1.checked_at == r2.checked_at == FIXED_NOW
