"""
Tests for the live risk-scoring pipeline wiring:

  execute_evidence_validation -> generate_feature_snapshot (now real,
  not a dummy stub) -> score_risk_task -> score_case (live CatBoost
  scoring) -> hydrate_review_queue (previously dead code, zero
  production callers).

Uses a tiny, genuinely-trained CatBoost model + isotonic calibrator
written to a temp artifacts/ directory (never the real champion
artifacts) so these tests don't depend on the offline training pipeline
having been run, and never touch any real model file.
"""

import json
import pickle
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.shared import Case, Merchant, ProcessingState
import app.models.module_a
import app.models.module_b
import app.models.module_c
import app.models.module_d
import app.models.module_g
import app.models.module_h
from app.models.module_a import Dispute
from app.models.module_e import CaseFeatureSnapshot, EValidationRunStatus, EvidencePolicyVersion, EvidenceValidationRun
from app.models.module_f import ModelDecisionPolicy, ModelVersion, RiskPrediction
from app.models.module_h import ReviewQueueItem

from app.services.feature_snapshot import generate_feature_snapshot
from app.services.ml.inference import score_case

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_f_risk_scoring_pipeline.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def clean_db():
    db = TestingSessionLocal()
    try:
        db.query(ReviewQueueItem).delete()
        db.query(RiskPrediction).delete()
        db.query(ModelDecisionPolicy).delete()
        db.query(ModelVersion).delete()
        db.query(CaseFeatureSnapshot).delete()
        db.query(EvidenceValidationRun).delete()
        db.query(EvidencePolicyVersion).delete()
        db.query(Dispute).delete()
        db.query(Case).delete()
        db.query(Merchant).delete()
        db.commit()
    finally:
        db.close()


def _make_case_with_snapshot(db, respond_in_days=5):
    merchant = Merchant(external_merchant_id=f"m_{uuid.uuid4()}", name="Test Merchant")
    db.add(merchant)
    db.commit()

    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="synthetic")
    db.add(case)
    db.commit()

    dispute = Dispute(
        case_id=case.case_id,
        external_dispute_id=case.external_dispute_id,
        payment_id="pay_1",
        amount_minor=50000,
        currency="INR",
        reason_code="10.4",
        status="open",
        dispute_created_at=datetime.now(timezone.utc),
        respond_by=datetime.now(timezone.utc) + timedelta(days=respond_in_days),
    )
    db.add(dispute)
    db.commit()

    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=datetime.now(timezone.utc),
    )
    db.add(policy)
    db.commit()

    run = EvidenceValidationRun(
        case_id=case.case_id, evidence_version="v1", policy_version_id=policy.policy_version_id,
        status=EValidationRunStatus.COMPLETED, started_at=datetime.now(timezone.utc),
        idempotency_key=f"val_{case.case_id}",
    )
    db.add(run)
    db.commit()

    snapshot = generate_feature_snapshot(db, run, results=[], assessments=[], links=[])
    db.add(snapshot)
    # Matches what execute_evidence_validation always guarantees before
    # score_risk_task is ever dispatched (app/worker/validation_tasks.py).
    case.processing_state = ProcessingState.FEATURE_READY
    db.commit()
    db.refresh(snapshot)
    return case, dispute, run, snapshot


# ---------------------------------------------------------------------------
# Layer 1: generate_feature_snapshot now computes real ml_features_v1,
# not the old dummy {"assessments_count": ...} placeholder.
# ---------------------------------------------------------------------------

def test_generate_feature_snapshot_produces_real_ml_features_not_dummy_counts():
    db = TestingSessionLocal()
    try:
        case, dispute, run, snapshot = _make_case_with_snapshot(db)

        assert "assessments_count" not in snapshot.features_json
        assert "results_count" not in snapshot.features_json
        assert "links_count" not in snapshot.features_json
        assert snapshot.feature_schema_version == "ml_features_v1"

        expected_keys = {
            "required_evidence_coverage", "missing_required_count", "evidence_count",
            "amount_match", "order_id_match", "tracking_match", "customer_match_score",
            "timeline_valid", "days_delivery_to_dispute", "contradiction_count",
            "high_severity_contradictions", "avg_ocr_confidence", "min_ocr_confidence",
            "document_quality_score", "reason_code", "payment_method", "dispute_amount",
            "disputed_amount_ratio", "refund_exists", "shipment_available", "days_to_deadline",
        }
        assert expected_keys == set(snapshot.features_json.keys())
        assert snapshot.features_json["reason_code"] == "10.4"
        assert snapshot.features_json["dispute_amount"] == 50000
        assert snapshot.features_json["refund_exists"] is False
        assert snapshot.features_json["shipment_available"] is False
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Layer 2: score_case() -- live scoring service.
# ---------------------------------------------------------------------------

def test_score_case_is_a_clean_noop_without_champion_artifacts(monkeypatch, tmp_path):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(tmp_path / "no_artifacts_here"))

    db = TestingSessionLocal()
    try:
        case, dispute, run, snapshot = _make_case_with_snapshot(db)

        result = score_case(db, case.case_id)

        assert result is None
        assert db.query(RiskPrediction).filter(RiskPrediction.case_id == case.case_id).count() == 0
    finally:
        db.close()


def test_score_case_returns_none_for_case_with_no_feature_snapshot(monkeypatch, tmp_path):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(tmp_path / "unused"))

    db = TestingSessionLocal()
    try:
        merchant = Merchant(external_merchant_id=f"m_{uuid.uuid4()}", name="Test Merchant")
        db.add(merchant)
        db.commit()
        case = Case(merchant_id=merchant.merchant_id, external_dispute_id="d1", source="synthetic")
        db.add(case)
        db.commit()

        assert score_case(db, case.case_id) is None
    finally:
        db.close()


@pytest.fixture
def fake_champion_artifacts_dir(tmp_path):
    """
    Writes a tiny, genuinely-trained CatBoost model + fitted isotonic
    calibrator + a decision policy to a temp directory, in exactly the
    layout champion_loader.py expects. Never touches the real artifacts/.
    """
    import catboost
    from sklearn.isotonic import IsotonicRegression

    from app.services.ml.training.preprocessor import Preprocessor

    examples = []
    for i in range(20):
        label = i % 2
        examples.append({
            "features": {
                "reason_code": "10.4",
                "payment_method": "card",
                "amount_match": True,
                "order_id_match": True,
                "tracking_match": True,
                "timeline_valid": True,
                "refund_exists": False,
                "shipment_available": True,
                "required_evidence_coverage": 1.0 if label else 0.2,
                "missing_required_count": 0 if label else 3,
                "evidence_count": 3 if label else 0,
                "customer_match_score": 0.9,
                "days_delivery_to_dispute": 2.0,
                "contradiction_count": 0,
                "high_severity_contradictions": 0,
                "avg_ocr_confidence": 0.95,
                "min_ocr_confidence": 0.9,
                "document_quality_score": 0.9,
                "dispute_amount": 10000,
                "disputed_amount_ratio": 1.0,
                "days_to_deadline": 5.0,
            },
            "label": label,
        })

    preprocessor = Preprocessor()
    preprocessor.fit(examples)
    X, y = preprocessor.transform(examples)

    model = catboost.CatBoostClassifier(
        iterations=10, depth=2, verbose=False, random_seed=42,
        cat_features=preprocessor.categorical_features,
    )
    model.fit(X, y)

    model_dir = tmp_path / "artifacts" / "runs" / "20990101_000000_v1"
    model_dir.mkdir(parents=True)
    model_path = model_dir / "model.cbm"
    model.save_model(str(model_path))

    raw_probs = model.predict_proba(X)[:, 1]
    calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    calibrator.fit(raw_probs, y)

    calib_dir = tmp_path / "artifacts" / "step13_calibration_20990101_000000_v1"
    calib_dir.mkdir(parents=True)
    with open(calib_dir / "calibrator.pkl", "wb") as f:
        pickle.dump(calibrator, f)

    policy_dir = tmp_path / "artifacts" / "step14_threshold_policy_20990101_000000_v1"
    policy_dir.mkdir(parents=True)
    with open(policy_dir / "decision_policy.json", "w") as f:
        json.dump({"t_accept": 0.4, "t_contest": 0.7, "c_fp": 50.0, "c_fn": 100.0, "c_review": 5.0}, f)

    return str(tmp_path / "artifacts")


def test_score_case_persists_real_risk_prediction_and_is_idempotent(monkeypatch, fake_champion_artifacts_dir):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", fake_champion_artifacts_dir)

    db = TestingSessionLocal()
    try:
        case, dispute, run, snapshot = _make_case_with_snapshot(db)

        prediction = score_case(db, case.case_id)
        db.commit()

        assert prediction is not None
        assert prediction.case_id == case.case_id
        assert prediction.feature_snapshot_id == snapshot.id
        assert 0.0 <= float(prediction.raw_score) <= 1.0
        assert 0.0 <= float(prediction.calibrated_probability) <= 1.0
        assert prediction.recommendation in ("ACCEPT", "REVIEW", "CONTEST")
        assert isinstance(prediction.hard_block, bool)

        model_version = db.query(ModelVersion).filter(ModelVersion.id == prediction.model_version_id).first()
        assert model_version.algorithm == "CatBoost"
        assert model_version.status == "champion"
        assert model_version.artifact_uri is not None

        policy = db.query(ModelDecisionPolicy).filter(ModelDecisionPolicy.id == prediction.decision_policy_id).first()
        assert float(policy.accept_threshold) == 0.4
        assert float(policy.contest_threshold) == 0.7
        assert policy.active is True

        # Idempotent: a second call returns the SAME row, not a duplicate.
        prediction_again = score_case(db, case.case_id)
        assert prediction_again.id == prediction.id
        assert db.query(RiskPrediction).filter(RiskPrediction.case_id == case.case_id).count() == 1
    finally:
        db.close()


def test_hard_block_true_when_mandatory_evidence_missing(monkeypatch, fake_champion_artifacts_dir):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", fake_champion_artifacts_dir)

    db = TestingSessionLocal()
    try:
        case, dispute, run, snapshot = _make_case_with_snapshot(db)
        # Force the stub-free, real-computed snapshot into a hard-block state.
        snapshot.features_json = {**snapshot.features_json, "missing_required_count": 2}
        db.add(snapshot)
        db.commit()

        prediction = score_case(db, case.case_id)
        db.commit()

        assert prediction.hard_block is True
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Layer 3: score_risk_task -- the previously-missing link to
# hydrate_review_queue (zero production callers before this change).
# ---------------------------------------------------------------------------

def test_score_risk_task_hydrates_the_review_queue_and_advances_processing_state(
    monkeypatch, fake_champion_artifacts_dir
):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", fake_champion_artifacts_dir)
    monkeypatch.setattr("app.worker.risk_scoring_tasks.SessionLocal", TestingSessionLocal)

    db = TestingSessionLocal()
    try:
        case, dispute, run, snapshot = _make_case_with_snapshot(db)
        case_id_str = str(case.case_id)
    finally:
        db.close()

    from app.worker.risk_scoring_tasks import score_risk_task
    result = score_risk_task(case_id_str)

    assert result["status"] == "SCORED"
    assert result["recommendation"] in ("ACCEPT", "REVIEW", "CONTEST")

    db = TestingSessionLocal()
    try:
        prediction = db.query(RiskPrediction).filter(RiskPrediction.case_id == case.case_id).first()
        assert prediction is not None

        queue_item = db.query(ReviewQueueItem).filter(ReviewQueueItem.case_id == case.case_id).first()
        assert queue_item is not None
        assert queue_item.prediction_id == prediction.id
        assert queue_item.priority_score > 0
        assert queue_item.respond_by is not None

        refreshed_case = db.query(Case).filter(Case.case_id == case.case_id).first()
        assert refreshed_case.processing_state == ProcessingState.RISK_SCORED
    finally:
        db.close()


def test_score_risk_task_is_a_clean_noop_without_champion_artifacts(monkeypatch, tmp_path):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(tmp_path / "no_artifacts_here"))
    monkeypatch.setattr("app.worker.risk_scoring_tasks.SessionLocal", TestingSessionLocal)

    db = TestingSessionLocal()
    try:
        case, dispute, run, snapshot = _make_case_with_snapshot(db)
        case_id_str = str(case.case_id)
    finally:
        db.close()

    from app.worker.risk_scoring_tasks import score_risk_task
    result = score_risk_task(case_id_str)

    assert result["status"] == "SKIPPED"

    db = TestingSessionLocal()
    try:
        assert db.query(ReviewQueueItem).filter(ReviewQueueItem.case_id == case.case_id).count() == 0
        refreshed_case = db.query(Case).filter(Case.case_id == case.case_id).first()
        # No regression: case stays exactly where it was (FEATURE_READY by
        # default in this fixture, since nothing moved it further).
        assert refreshed_case.processing_state != ProcessingState.RISK_SCORED
    finally:
        db.close()
