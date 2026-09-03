import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.core.database import Base, get_db
from app.core.config import settings
from app.models.shared import Merchant, AppUser, AppUserRole
from app.models.module_a import Dispute
from app.models.shared import Case, ProcessingState
from app.models.module_f import RiskPrediction
from app.models.module_h import (
    ContestPackage, ContestPackageStatus, ContestSubmission, SubmissionStatus,
    DisputeOutcome, DisputeOutcomeEnum, CuratedFeedbackLabel, LabelQuality,
)

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_k.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_overrides():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()


client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def generate_signature(payload_str: str) -> str:
    return hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"), payload_str.encode("utf-8"), hashlib.sha256,
    ).hexdigest()


def make_outcome_payload(event: str, dispute_id: str, status: str, amount_deducted=None):
    return {
        "event": event,
        "payload": {"dispute": {"entity": {"id": dispute_id, "status": status, "amount_deducted": amount_deducted}}},
        "created_at": int(time.time()),
    }


def post_outcome(payload: dict, event_id="evt_default", signature=None):
    payload_str = json.dumps(payload)
    sig = signature if signature is not None else generate_signature(payload_str)
    headers = {"X-Razorpay-Signature": sig}
    if event_id is not None:
        headers["X-Razorpay-Event-Id"] = event_id
    return client.post("/api/v1/webhooks/razorpay-outcome", data=payload_str, headers=headers)


def make_merchant(db):
    merchant = Merchant(external_merchant_id=f"ext_{uuid.uuid4()}", name="Test Merchant K", is_active=True)
    db.add(merchant)
    db.commit()
    db.refresh(merchant)
    return merchant


def make_case_with_dispute(db, merchant, external_dispute_id):
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=external_dispute_id, source="razorpay", processing_state=ProcessingState.INGESTED)
    db.add(case)
    db.commit()
    db.refresh(case)

    dispute = Dispute(
        case_id=case.case_id, external_dispute_id=external_dispute_id, payment_id="pay_x",
        amount_minor=10000, currency="INR", reason_code="fraud", status="won",
        dispute_created_at=datetime.now(timezone.utc),
    )
    db.add(dispute)
    db.commit()
    return case, dispute


def make_submitted_contest_submission(db, case, external_dispute_id, status=SubmissionStatus.SUCCESS):
    package = ContestPackage(
        case_id=case.case_id, review_action_id=uuid.uuid4(), draft_id=uuid.uuid4(),
        contest_amount_minor=5000, summary="Evidence supports contest.",
        package_hash=hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest(),
        status=ContestPackageStatus.SUBMITTED,
    )
    db.add(package)
    db.commit()
    db.refresh(package)

    submission = ContestSubmission(
        contest_package_id=package.id, external_dispute_id=external_dispute_id, action="submit",
        external_status="under_review", submitted_at=datetime.now(timezone.utc),
        razorpay_evidence_json={}, response_snapshot={"id": external_dispute_id}, status=status,
    )
    db.add(submission)
    db.commit()
    return package, submission


def make_prediction(db, case):
    prediction = RiskPrediction(
        case_id=case.case_id, feature_snapshot_id=uuid.uuid4(), model_version_id=uuid.uuid4(),
        decision_policy_id=uuid.uuid4(), raw_score=0.9, calibrated_probability=0.9,
        recommendation="CONTEST", hard_block=False, idempotency_key=f"pred_{uuid.uuid4()}",
    )
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return prediction


def make_model_maintainer(db, merchant):
    user = AppUser(merchant_id=merchant.merchant_id, email=f"mm_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.MODEL_MAINTAINER)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_approver(db, merchant):
    user = AppUser(merchant_id=merchant.merchant_id, email=f"appr_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.APPROVER)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# 1. Valid won webhook, resolved via ContestSubmission -> one DisputeOutcome
def test_won_webhook_resolves_via_contest_submission():
    db = TestingSessionLocal()
    merchant = make_merchant(db)
    case, dispute = make_case_with_dispute(db, merchant, "disp_won_1")
    package, submission = make_submitted_contest_submission(db, case, "disp_won_1")
    prediction = make_prediction(db, case)

    payload = make_outcome_payload("payment.dispute.won", "disp_won_1", "won", amount_deducted=None)
    response = post_outcome(payload, event_id="evt_won_1")
    assert response.status_code == 202
    assert response.json()["status"] == "success"

    outcome = db.query(DisputeOutcome).filter(DisputeOutcome.source_event_id == "evt_won_1").first()
    assert outcome is not None
    assert outcome.outcome == DisputeOutcomeEnum.WON
    assert outcome.case_id == case.case_id
    assert outcome.contest_submission_id == submission.id
    assert outcome.prediction_id == prediction.id


# 2. Valid lost webhook -> amount_deducted_minor populated
def test_lost_webhook_populates_amount_deducted():
    db = TestingSessionLocal()
    merchant = make_merchant(db)
    case, dispute = make_case_with_dispute(db, merchant, "disp_lost_1")
    make_submitted_contest_submission(db, case, "disp_lost_1")

    payload = make_outcome_payload("payment.dispute.lost", "disp_lost_1", "lost", amount_deducted=7500)
    response = post_outcome(payload, event_id="evt_lost_1")
    assert response.status_code == 202

    outcome = db.query(DisputeOutcome).filter(DisputeOutcome.source_event_id == "evt_lost_1").first()
    assert outcome.outcome == DisputeOutcomeEnum.LOST
    assert outcome.amount_deducted_minor == 7500


# 3. Valid closed webhook
def test_closed_webhook():
    db = TestingSessionLocal()
    merchant = make_merchant(db)
    case, dispute = make_case_with_dispute(db, merchant, "disp_closed_1")
    make_submitted_contest_submission(db, case, "disp_closed_1")

    payload = make_outcome_payload("payment.dispute.closed", "disp_closed_1", "closed")
    response = post_outcome(payload, event_id="evt_closed_1")
    assert response.status_code == 202

    outcome = db.query(DisputeOutcome).filter(DisputeOutcome.source_event_id == "evt_closed_1").first()
    assert outcome.outcome == DisputeOutcomeEnum.CLOSED


# 4. Duplicate X-Razorpay-Event-Id -> no second row
def test_duplicate_event_id_no_second_row():
    db = TestingSessionLocal()
    merchant = make_merchant(db)
    case, dispute = make_case_with_dispute(db, merchant, "disp_dup_1")
    make_submitted_contest_submission(db, case, "disp_dup_1")

    payload = make_outcome_payload("payment.dispute.won", "disp_dup_1", "won")
    r1 = post_outcome(payload, event_id="evt_dup_1")
    assert r1.status_code == 202
    assert r1.json()["status"] == "success"

    r2 = post_outcome(payload, event_id="evt_dup_1")
    assert r2.status_code == 202
    assert r2.json()["status"] == "ignored"

    assert db.query(DisputeOutcome).filter(DisputeOutcome.source_event_id == "evt_dup_1").count() == 1


# 5. Missing/invalid signature -> 401
def test_missing_signature_rejected():
    payload = make_outcome_payload("payment.dispute.won", "disp_x", "won")
    payload_str = json.dumps(payload)
    response = client.post(
        "/api/v1/webhooks/razorpay-outcome", data=payload_str,
        headers={"X-Razorpay-Event-Id": "evt_no_sig"},
    )
    assert response.status_code == 401


def test_invalid_signature_rejected():
    payload = make_outcome_payload("payment.dispute.won", "disp_x", "won")
    response = post_outcome(payload, event_id="evt_bad_sig", signature="not_a_real_signature")
    assert response.status_code == 401
    db = TestingSessionLocal()
    assert db.query(DisputeOutcome).count() == 0


# 6. Missing event-ID header -> reject safely, no fabricated ID
def test_missing_event_id_header_rejected():
    payload = make_outcome_payload("payment.dispute.won", "disp_x", "won")
    payload_str = json.dumps(payload)
    sig = generate_signature(payload_str)
    response = client.post(
        "/api/v1/webhooks/razorpay-outcome", data=payload_str, headers={"X-Razorpay-Signature": sig},
    )
    assert response.status_code == 400
    db = TestingSessionLocal()
    assert db.query(DisputeOutcome).count() == 0


# 7. Unresolvable ContestSubmission but Dispute exists -> outcome retained, no fabricated links
def test_unresolved_submission_falls_back_to_dispute_case_id():
    db = TestingSessionLocal()
    merchant = make_merchant(db)
    case, dispute = make_case_with_dispute(db, merchant, "disp_no_submission")
    # No ContestSubmission created for this dispute.

    payload = make_outcome_payload("payment.dispute.won", "disp_no_submission", "won")
    response = post_outcome(payload, event_id="evt_no_submission")
    assert response.status_code == 202

    outcome = db.query(DisputeOutcome).filter(DisputeOutcome.source_event_id == "evt_no_submission").first()
    assert outcome is not None
    assert outcome.case_id == case.case_id
    assert outcome.contest_submission_id is None
    assert outcome.prediction_id is None


# 7b. Fully unresolvable (no ContestSubmission, no Dispute) -> 422, no row written
def test_fully_unresolvable_case_returns_422_and_writes_nothing():
    payload = make_outcome_payload("payment.dispute.won", "disp_totally_unknown", "won")
    response = post_outcome(payload, event_id="evt_totally_unknown")
    assert response.status_code == 422

    db = TestingSessionLocal()
    assert db.query(DisputeOutcome).filter(DisputeOutcome.source_event_id == "evt_totally_unknown").count() == 0


# 8. Correct case/prediction resolution via ContestSubmission chain (see test 1); ignored-event-type case:
def test_unhandled_event_type_is_ignored():
    db = TestingSessionLocal()
    merchant = make_merchant(db)
    case, dispute = make_case_with_dispute(db, merchant, "disp_other")
    make_submitted_contest_submission(db, case, "disp_other")

    payload = make_outcome_payload("payment.dispute.action_required", "disp_other", "action_required")
    response = post_outcome(payload, event_id="evt_other")
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    assert db.query(DisputeOutcome).count() == 0


# 9. MODEL_MAINTAINER curation succeeds
def test_model_maintainer_curation_succeeds():
    db = TestingSessionLocal()
    merchant = make_merchant(db)
    case, dispute = make_case_with_dispute(db, merchant, "disp_curate_1")
    make_submitted_contest_submission(db, case, "disp_curate_1")
    payload = make_outcome_payload("payment.dispute.won", "disp_curate_1", "won")
    post_outcome(payload, event_id="evt_curate_1")
    outcome = db.query(DisputeOutcome).filter(DisputeOutcome.source_event_id == "evt_curate_1").first()

    maintainer = make_model_maintainer(db, merchant)
    response = client.post(
        f"/api/v1/outcomes/{outcome.id}/curate",
        json={"label_name": "final_outcome", "label_value": "won", "label_quality": "GOLD", "approved_for_training": True},
        headers={"X-User-Id": str(maintainer.user_id)},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["version"] == 1
    assert body["approved_for_training"] is True

    label = db.query(CuratedFeedbackLabel).filter(CuratedFeedbackLabel.outcome_id == outcome.id).first()
    assert label is not None
    assert label.curated_by == maintainer.user_id
    assert label.label_quality == LabelQuality.GOLD


# 10. Non-MODEL_MAINTAINER -> 403
def test_non_model_maintainer_forbidden():
    db = TestingSessionLocal()
    merchant = make_merchant(db)
    case, dispute = make_case_with_dispute(db, merchant, "disp_curate_2")
    make_submitted_contest_submission(db, case, "disp_curate_2")
    payload = make_outcome_payload("payment.dispute.won", "disp_curate_2", "won")
    post_outcome(payload, event_id="evt_curate_2")
    outcome = db.query(DisputeOutcome).filter(DisputeOutcome.source_event_id == "evt_curate_2").first()

    approver = make_approver(db, merchant)
    response = client.post(
        f"/api/v1/outcomes/{outcome.id}/curate",
        json={"label_name": "final_outcome", "label_value": "won", "label_quality": "GOLD"},
        headers={"X-User-Id": str(approver.user_id)},
    )
    assert response.status_code == 403
    assert db.query(CuratedFeedbackLabel).count() == 0


# 11. approved_for_training can never be enabled through webhook ingestion
def test_webhook_ingestion_never_creates_curated_label():
    db = TestingSessionLocal()
    merchant = make_merchant(db)
    case, dispute = make_case_with_dispute(db, merchant, "disp_no_curation")
    make_submitted_contest_submission(db, case, "disp_no_curation")

    payload = make_outcome_payload("payment.dispute.won", "disp_no_curation", "won")
    response = post_outcome(payload, event_id="evt_no_curation")
    assert response.status_code == 202

    assert db.query(CuratedFeedbackLabel).count() == 0
    assert db.query(CuratedFeedbackLabel).filter(CuratedFeedbackLabel.approved_for_training == True).count() == 0

    import inspect
    import app.services.outcome_feedback.outcome_ingestion as ingestion_module
    assert "CuratedFeedbackLabel" not in inspect.getsource(ingestion_module)


# 12. Curation version increments correctly
def test_curation_version_increments():
    db = TestingSessionLocal()
    merchant = make_merchant(db)
    case, dispute = make_case_with_dispute(db, merchant, "disp_version_1")
    make_submitted_contest_submission(db, case, "disp_version_1")
    payload = make_outcome_payload("payment.dispute.won", "disp_version_1", "won")
    post_outcome(payload, event_id="evt_version_1")
    outcome = db.query(DisputeOutcome).filter(DisputeOutcome.source_event_id == "evt_version_1").first()
    maintainer = make_model_maintainer(db, merchant)

    r1 = client.post(
        f"/api/v1/outcomes/{outcome.id}/curate",
        json={"label_name": "final_outcome", "label_value": "won", "label_quality": "SILVER"},
        headers={"X-User-Id": str(maintainer.user_id)},
    )
    assert r1.json()["version"] == 1

    r2 = client.post(
        f"/api/v1/outcomes/{outcome.id}/curate",
        json={"label_name": "final_outcome", "label_value": "won_corrected", "label_quality": "GOLD"},
        headers={"X-User-Id": str(maintainer.user_id)},
    )
    assert r2.json()["version"] == 2

    # A different label_name starts its own version sequence at 1.
    r3 = client.post(
        f"/api/v1/outcomes/{outcome.id}/curate",
        json={"label_name": "other_label", "label_value": "x", "label_quality": "SYNTHETIC"},
        headers={"X-User-Id": str(maintainer.user_id)},
    )
    assert r3.json()["version"] == 1


# 13. DB uniqueness constraint rejects duplicate source_event_id independently of service logic
def test_db_unique_constraint_source_event_id():
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    db = TestingSessionLocal()
    merchant = make_merchant(db)
    case, dispute = make_case_with_dispute(db, merchant, "disp_db_unique")
    try:
        o1 = DisputeOutcome(
            case_id=case.case_id, outcome=DisputeOutcomeEnum.WON,
            source_event_id="evt_db_unique", occurred_at=datetime.now(timezone.utc),
        )
        db.add(o1)
        db.commit()

        o2 = DisputeOutcome(
            case_id=case.case_id, outcome=DisputeOutcomeEnum.LOST,
            source_event_id="evt_db_unique", occurred_at=datetime.now(timezone.utc),
        )
        db.add(o2)
        with pytest.raises(SAIntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()
