import pytest
import uuid
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import get_db, Base
from app.core.config import settings
from app.models.shared import Merchant, Case, AppUser, AppUserRole
from app.models.module_a import Dispute
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidencePolicyVersion, EValidationRunStatus
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy
from app.models.module_h import ReviewQueueItem, QueueStatus, ReviewActionEnum, ReviewAction

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_h_05_pg"
TEST_DB_URL = f"postgresql://resolve_user:resolve_password@127.0.0.1:5433/{TEST_DB_NAME}"

HIGH_AMOUNT = settings.DUAL_CONTROL_AMOUNT_THRESHOLD_MINOR  # >= threshold
LOW_AMOUNT = settings.DUAL_CONTROL_AMOUNT_THRESHOLD_MINOR - 1  # below threshold


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


def setup_base_data(db: Session, merchant_name="Test Merchant H05"):
    merchant = Merchant(name=merchant_name, external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()

    approver1 = AppUser(merchant_id=merchant.merchant_id, email=f"a1_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.APPROVER)
    approver2 = AppUser(merchant_id=merchant.merchant_id, email=f"a2_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.APPROVER)
    approver3 = AppUser(merchant_id=merchant.merchant_id, email=f"a3_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.APPROVER)
    risk_analyst = AppUser(merchant_id=merchant.merchant_id, email=f"ra_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.RISK_ANALYST)
    db.add_all([approver1, approver2, approver3, risk_analyst])

    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.flush()

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

    return merchant, approver1, approver2, approver3, risk_analyst, case, policy_version, model_version, decision_policy, validation_run


def create_queue_item(db: Session, case: Case, model_version: ModelVersion, decision_policy: ModelDecisionPolicy,
                       validation_run: EvidenceValidationRun, amount_minor=LOW_AMOUNT,
                       recommendation="CONTEST", hard_block=False):
    dispute = Dispute(
        case_id=case.case_id, external_dispute_id=case.external_dispute_id, payment_id="pay_1",
        amount_minor=amount_minor, currency="INR", reason_code="fraud", status="open",
        dispute_created_at=datetime.now(timezone.utc), respond_by=datetime.now(timezone.utc) + timedelta(days=1)
    )
    db.add(dispute)

    snapshot = CaseFeatureSnapshot(
        case_id=case.case_id,
        validation_run_id=validation_run.id,
        feature_schema_version="v1",
        feature_hash="hash",
        features_json={"amount": amount_minor},
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
        recommendation=recommendation,
        hard_block=hard_block,
        idempotency_key=f"pred_{uuid.uuid4()}"
    )
    db.add(prediction)
    db.flush()

    queue_item = ReviewQueueItem(
        case_id=case.case_id,
        prediction_id=prediction.id,
        priority_score=100,
        queue_status=QueueStatus.PENDING,
        respond_by=datetime.now(timezone.utc) + timedelta(days=1)
    )
    db.add(queue_item)
    db.commit()
    return queue_item, prediction, dispute


# 1. Normal (non-gated) case is unaffected: single approver finalizes immediately.
def test_normal_case_single_approval_unaffected(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=LOW_AMOUNT, hard_block=False)

    headers = {"X-User-Id": str(a1.user_id)}
    response = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers, json={"action": "APPROVE_CONTEST"})
    assert response.status_code == 201
    assert response.json()["dual_approval_status"] is None

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.DONE


# 2. High amount triggers pending second approval.
def test_high_amount_triggers_pending_second_approval(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT, hard_block=False)

    headers = {"X-User-Id": str(a1.user_id)}
    response = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers, json={"action": "APPROVE_CONTEST"})
    assert response.status_code == 201
    assert response.json()["dual_approval_status"] == "AWAITING_SECOND_APPROVAL"

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.PENDING_SECOND_APPROVAL
    assert qi.pending_review_action_id is not None


# 3. Hard-block override triggers dual control even at a low amount.
def test_hard_block_override_triggers_dual_control_even_low_amount(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=LOW_AMOUNT, hard_block=True)

    headers = {"X-User-Id": str(a1.user_id)}
    payload = {"action": "APPROVE_CONTEST", "override_reason_code": "HARD_BLOCK_OVERRIDE", "notes": "evidence is strong despite block"}
    response = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers, json=payload)
    assert response.status_code == 201
    assert response.json()["dual_approval_status"] == "AWAITING_SECOND_APPROVAL"

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.PENDING_SECOND_APPROVAL


# 4. APPROVE_ACCEPT under hard_block is not itself an override -> not gated by hard-block alone.
def test_hard_block_approve_accept_not_gated_by_hardblock_alone(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    # recommendation="ACCEPT" so APPROVE_ACCEPT doesn't independently trip the unrelated
    # H-18 "CONTEST recommendation but APPROVE_ACCEPT action" override rule.
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=LOW_AMOUNT, recommendation="ACCEPT", hard_block=True)

    headers = {"X-User-Id": str(a1.user_id)}
    response = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers, json={"action": "APPROVE_ACCEPT"})
    assert response.status_code == 201
    assert response.json()["dual_approval_status"] is None

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.DONE


# 5. A second, distinct active APPROVER finalizes the decision.
def test_second_distinct_approver_finalizes(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT)

    headers1 = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={"action": "APPROVE_CONTEST"})
    assert r1.status_code == 201

    headers2 = {"X-User-Id": str(a2.user_id)}
    r2 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers2, json={"action": "APPROVE_CONTEST"})
    assert r2.status_code == 201
    assert r2.json()["dual_approval_status"] == "FINALIZED"
    assert r2.json()["reviewer_id"] == str(a2.user_id)

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.DONE

    actions = db.query(ReviewAction).filter(ReviewAction.queue_item_id == qi.id).all()
    assert len(actions) == 2


# 6. The same reviewer cannot provide both approvals.
def test_same_approver_cannot_provide_second_approval(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT)

    headers = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers, json={"action": "APPROVE_CONTEST"})
    assert r1.status_code == 201

    r2 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers, json={"action": "APPROVE_CONTEST"})
    assert r2.status_code == 400
    assert "different active approver" in r2.json()["detail"].lower()

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.PENDING_SECOND_APPROVAL


# 7. A mismatched action (not ESCALATE) while pending is rejected.
def test_second_approval_mismatched_action_rejected(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT, recommendation="CONTEST")

    headers1 = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={"action": "APPROVE_CONTEST"})
    assert r1.status_code == 201

    headers2 = {"X-User-Id": str(a2.user_id)}
    r2 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers2, json={
        "action": "APPROVE_ACCEPT", "override_reason_code": "disagree", "notes": "I think we should accept"
    })
    assert r2.status_code == 400
    assert "pending second approval" in r2.json()["detail"].lower()

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.PENDING_SECOND_APPROVAL


# 8a. ESCALATE by a different approver cancels the pending state without finalizing.
def test_escalate_by_different_approver_cancels_pending_second_approval(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT)

    headers1 = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={"action": "APPROVE_CONTEST"})
    assert r1.status_code == 201

    headers2 = {"X-User-Id": str(a2.user_id)}
    r2 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers2, json={"action": "ESCALATE"})
    assert r2.status_code == 201
    assert r2.json()["dual_approval_status"] == "ESCALATED_CANCELLED"

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.DONE

    actions = db.query(ReviewAction).filter(ReviewAction.queue_item_id == qi.id).all()
    assert len(actions) == 2
    assert not any(a.action == ReviewActionEnum.APPROVE_CONTEST and a.reviewer_id == a2.user_id for a in actions)


# 8b. Frozen decision: the ORIGINAL first approver may also ESCALATE their own pending approval.
def test_escalate_by_original_first_approver_also_cancels_pending(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT)

    headers1 = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={"action": "APPROVE_CONTEST"})
    assert r1.status_code == 201

    r2 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={"action": "ESCALATE"})
    assert r2.status_code == 201
    assert r2.json()["dual_approval_status"] == "ESCALATED_CANCELLED"
    assert r2.json()["reviewer_id"] == str(a1.user_id)

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.DONE


# 9. Non-decision actions (REQUEST_MORE_EVIDENCE / EDIT_DRAFT) are blocked while pending.
def test_non_decision_action_blocked_while_pending(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT)

    headers1 = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={"action": "APPROVE_CONTEST"})
    assert r1.status_code == 201

    headers2 = {"X-User-Id": str(a2.user_id)}
    for action in ["REQUEST_MORE_EVIDENCE", "EDIT_DRAFT"]:
        payload = {"action": action}
        if action == "EDIT_DRAFT":
            payload["draft_revision_json"] = {"x": "y"}
        r = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers2, json=payload)
        assert r.status_code == 400, f"{action} should be blocked while pending"

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.PENDING_SECOND_APPROVAL


# 10. H-18 justification is independently required on the second approval too.
def test_h18_justification_required_independently_on_second_approval(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    # hard_block=True + APPROVE_CONTEST => triggers both H-18 override AND H-05 dual control
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=LOW_AMOUNT, hard_block=True)

    headers1 = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={
        "action": "APPROVE_CONTEST", "override_reason_code": "HARD_BLOCK_OVERRIDE", "notes": "first approver justification"
    })
    assert r1.status_code == 201
    assert r1.json()["dual_approval_status"] == "AWAITING_SECOND_APPROVAL"

    headers2 = {"X-User-Id": str(a2.user_id)}
    # Second approver omits override fields -> must be rejected even though first supplied theirs.
    r2 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers2, json={"action": "APPROVE_CONTEST"})
    assert r2.status_code == 400
    assert "override_reason_code" in r2.json()["detail"]

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.PENDING_SECOND_APPROVAL

    # Now with its own justification, it succeeds.
    r3 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers2, json={
        "action": "APPROVE_CONTEST", "override_reason_code": "HARD_BLOCK_OVERRIDE", "notes": "second approver's own justification"
    })
    assert r3.status_code == 201
    assert r3.json()["dual_approval_status"] == "FINALIZED"


# 11. Tenant isolation preserved for the second-approval submission.
def test_dual_control_tenant_isolation(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT)

    headers1 = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={"action": "APPROVE_CONTEST"})
    assert r1.status_code == 201

    merchant2 = Merchant(name="Other Merchant H05", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant2)
    db.flush()
    other_approver = AppUser(merchant_id=merchant2.merchant_id, email=f"other_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.APPROVER)
    db.add(other_approver)
    db.commit()

    headers_other = {"X-User-Id": str(other_approver.user_id)}
    r2 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers_other, json={"action": "APPROVE_CONTEST"})
    assert r2.status_code == 403

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.PENDING_SECOND_APPROVAL


# 12. RBAC unchanged: a non-APPROVER role cannot submit the second approval either.
def test_dual_control_rbac_unchanged(client, db):
    merchant, a1, a2, a3, risk_analyst, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT)

    headers1 = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={"action": "APPROVE_CONTEST"})
    assert r1.status_code == 201

    headers_ra = {"X-User-Id": str(risk_analyst.user_id)}
    r2 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers_ra, json={"action": "APPROVE_CONTEST"})
    assert r2.status_code == 403

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.PENDING_SECOND_APPROVAL


# 13. Once finalized, a further second-approval attempt sees DONE and is rejected (race outcome).
def test_concurrent_second_approval_only_one_wins(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT)

    headers1 = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={"action": "APPROVE_CONTEST"})
    assert r1.status_code == 201

    headers2 = {"X-User-Id": str(a2.user_id)}
    r2 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers2, json={"action": "APPROVE_CONTEST"})
    assert r2.status_code == 201

    headers3 = {"X-User-Id": str(a3.user_id)}
    r3 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers3, json={"action": "APPROVE_CONTEST"})
    assert r3.status_code == 400
    assert "already done" in r3.json()["detail"].lower()


# 14. A rejected/invalid second attempt leaves the pending state untouched (transactional).
def test_pending_state_survives_rejected_second_attempt(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT)

    headers1 = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={"action": "APPROVE_CONTEST"})
    assert r1.status_code == 201
    first_action_id = r1.json()["id"]

    headers2 = {"X-User-Id": str(a2.user_id)}
    r2 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers2, json={"action": "APPROVE_ACCEPT"})
    assert r2.status_code == 400

    db.refresh(qi)
    assert qi.queue_status == QueueStatus.PENDING_SECOND_APPROVAL
    assert str(qi.pending_review_action_id) == first_action_id

    actions = db.query(ReviewAction).filter(ReviewAction.queue_item_id == qi.id).all()
    assert len(actions) == 1  # the rejected attempt was never committed


# 15. Amount threshold boundary: exactly at threshold triggers, one paisa below does not.
def test_amount_threshold_boundary(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)

    qi_high, _, _ = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT)
    headers = {"X-User-Id": str(a1.user_id)}
    r_high = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers, json={"action": "APPROVE_CONTEST"})
    assert r_high.status_code == 201
    assert r_high.json()["dual_approval_status"] == "AWAITING_SECOND_APPROVAL"

    case2 = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case2)
    db.flush()
    # case_feature_snapshots has a UNIQUE constraint on validation_run_id alone,
    # so this second case needs its own validation run.
    validation_run2 = EvidenceValidationRun(
        case_id=case2.case_id,
        evidence_version="v1",
        policy_version_id=validation_run.policy_version_id,
        status=EValidationRunStatus.COMPLETED,
        started_at=datetime.now(timezone.utc),
        idempotency_key=f"val_{uuid.uuid4()}"
    )
    db.add(validation_run2)
    db.commit()
    qi_low, _, _ = create_queue_item(db, case2, model_version, decision_policy, validation_run2, amount_minor=LOW_AMOUNT)
    r_low = client.post(f"/api/v1/cases/{case2.case_id}/review-action", headers=headers, json={"action": "APPROVE_CONTEST"})
    assert r_low.status_code == 201
    assert r_low.json()["dual_approval_status"] is None


# 16. Both approval events are independently, durably auditable.
def test_both_approvals_independently_recorded(client, db):
    merchant, a1, a2, a3, _, case, _, model_version, decision_policy, validation_run = setup_base_data(db)
    qi, pred, dispute = create_queue_item(db, case, model_version, decision_policy, validation_run, amount_minor=HIGH_AMOUNT)

    headers1 = {"X-User-Id": str(a1.user_id)}
    r1 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers1, json={"action": "APPROVE_CONTEST", "notes": "first"})
    headers2 = {"X-User-Id": str(a2.user_id)}
    r2 = client.post(f"/api/v1/cases/{case.case_id}/review-action", headers=headers2, json={"action": "APPROVE_CONTEST", "notes": "second"})

    actions = db.query(ReviewAction).filter(ReviewAction.queue_item_id == qi.id).order_by(ReviewAction.created_at).all()
    assert len(actions) == 2
    assert actions[0].id != actions[1].id
    assert actions[0].reviewer_id == a1.user_id
    assert actions[1].reviewer_id == a2.user_id
    assert actions[0].notes == "first"
    assert actions[1].notes == "second"
