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
from app.models.module_f import ModelDecisionPolicy, ModelMetric, ModelVersion, PredictionExplanation, RiskPrediction
from app.models.module_h import ReviewQueueItem

from app.services.feature_snapshot import generate_feature_snapshot
from app.services.ml.inference import score_case
from app.services.mlops.model_registry import promote_candidate_from_disk, rollback_to_previous_champion

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
        db.query(PredictionExplanation).delete()
        db.query(RiskPrediction).delete()
        db.query(ModelMetric).delete()
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


def _write_fake_artifacts(
    artifacts_root, run_id, t_accept=0.4, t_contest=0.7,
    c_fp=50.0, c_fn=100.0, c_review=5.0, holdout_expected_cost=None,
):
    """
    Writes a tiny, genuinely-trained CatBoost model + fitted isotonic
    calibrator + a decision policy (and, optionally, a step15-shaped
    holdout evaluation) under artifacts_root, in exactly the layout
    champion_loader.py expects. Never touches the real artifacts/.
    Call with a lexicographically-later run_id to simulate a fresher
    regeneration landing on disk.
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

    model_dir = artifacts_root / "runs" / run_id
    model_dir.mkdir(parents=True)
    model_path = model_dir / "model.cbm"
    model.save_model(str(model_path))

    raw_probs = model.predict_proba(X)[:, 1]
    calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    calibrator.fit(raw_probs, y)

    calib_dir = artifacts_root / f"step13_calibration_{run_id}"
    calib_dir.mkdir(parents=True)
    with open(calib_dir / "calibrator.pkl", "wb") as f:
        pickle.dump(calibrator, f)

    policy_dir = artifacts_root / f"step14_threshold_policy_{run_id}"
    policy_dir.mkdir(parents=True)
    with open(policy_dir / "decision_policy.json", "w") as f:
        json.dump({"t_accept": t_accept, "t_contest": t_contest, "c_fp": c_fp, "c_fn": c_fn, "c_review": c_review}, f)

    if holdout_expected_cost is not None:
        eval_dir = artifacts_root / f"step15_final_holdout_eval_{run_id}"
        eval_dir.mkdir(parents=True)
        with open(eval_dir / "final_evaluation.json", "w") as f:
            json.dump({
                "champion_model": {"run_dir": run_id},
                "metrics": {
                    "expected_cost": holdout_expected_cost,
                    "precision": 1.0,
                    "recall": 0.5,
                    "f1": 0.6,
                    "brier_score_calibrated": 0.1,
                },
                "decision_policy": {
                    "step14_dir": f"step14_threshold_policy_{run_id}",
                    "c_fp": c_fp, "c_fn": c_fn, "c_review": c_review,
                },
            }, f)


@pytest.fixture
def fake_champion_artifacts_dir(tmp_path):
    artifacts_root = tmp_path / "artifacts"
    _write_fake_artifacts(artifacts_root, "20990101_000000_v1")
    return str(artifacts_root)


def test_score_case_persists_real_risk_prediction_and_is_idempotent(monkeypatch, fake_champion_artifacts_dir):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", fake_champion_artifacts_dir)

    db = TestingSessionLocal()
    try:
        case, dispute, run, snapshot = _make_case_with_snapshot(db)

        decision = promote_candidate_from_disk(db)
        assert decision.promoted is True

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

        explanations = (
            db.query(PredictionExplanation)
            .filter(PredictionExplanation.prediction_id == prediction.id)
            .order_by(PredictionExplanation.rank)
            .all()
        )
        assert 1 <= len(explanations) <= 5
        assert [e.rank for e in explanations] == list(range(1, len(explanations) + 1))
        for explanation in explanations:
            assert explanation.feature_name
            assert explanation.contribution is not None
            assert explanation.direction in ("INCREASES_RISK", "DECREASES_RISK", "NEUTRAL")
            assert explanation.display_text

        # Ranked by |contribution| descending.
        magnitudes = [abs(float(e.contribution)) for e in explanations]
        assert magnitudes == sorted(magnitudes, reverse=True)

        # Did not get duplicated by the idempotent second score_case() call.
        assert (
            db.query(PredictionExplanation).filter(PredictionExplanation.prediction_id == prediction.id).count()
            == len(explanations)
        )
    finally:
        db.close()


def test_score_case_persists_prediction_even_if_shap_explanation_fails(monkeypatch, fake_champion_artifacts_dir):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", fake_champion_artifacts_dir)
    monkeypatch.setattr(
        "app.services.ml.inference.compute_shap_explanations",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    db = TestingSessionLocal()
    try:
        case, dispute, run, snapshot = _make_case_with_snapshot(db)
        promote_candidate_from_disk(db)

        prediction = score_case(db, case.case_id)
        db.commit()

        assert prediction is not None
        assert db.query(PredictionExplanation).filter(
            PredictionExplanation.prediction_id == prediction.id
        ).count() == 0
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

        promote_candidate_from_disk(db)

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
        case_uuid = case.case_id
        case_id_str = str(case_uuid)
        promote_candidate_from_disk(db)
    finally:
        db.close()

    from app.worker.risk_scoring_tasks import score_risk_task
    result = score_risk_task(case_id_str)

    assert result["status"] == "SCORED"
    assert result["recommendation"] in ("ACCEPT", "REVIEW", "CONTEST")

    db = TestingSessionLocal()
    try:
        prediction = db.query(RiskPrediction).filter(RiskPrediction.case_id == case_uuid).first()
        assert prediction is not None

        queue_item = db.query(ReviewQueueItem).filter(ReviewQueueItem.case_id == case_uuid).first()
        assert queue_item is not None
        assert queue_item.prediction_id == prediction.id
        assert queue_item.priority_score > 0
        assert queue_item.respond_by is not None

        refreshed_case = db.query(Case).filter(Case.case_id == case_uuid).first()
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


# ---------------------------------------------------------------------------
# Layer 4: promote_candidate_from_disk / rollback_to_previous_champion --
# the promotion guardrail. Before this, whatever was newest on disk became
# champion unconditionally the instant live scoring next ran.
# ---------------------------------------------------------------------------

def test_promote_first_ever_candidate_without_holdout_metrics(monkeypatch, tmp_path):
    from app.core.config import settings
    artifacts_root = tmp_path / "artifacts"
    _write_fake_artifacts(artifacts_root, "20990101_000000_v1")
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(artifacts_root))

    db = TestingSessionLocal()
    try:
        decision = promote_candidate_from_disk(db)
        assert decision.promoted is True
        assert decision.model_version.status == "champion"
        assert decision.decision_policy.active is True
    finally:
        db.close()


def test_promote_rejects_worse_candidate_and_keeps_existing_champion(monkeypatch, tmp_path):
    from app.core.config import settings
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(artifacts_root))

    db = TestingSessionLocal()
    try:
        _write_fake_artifacts(artifacts_root, "20990101_000000_v1", holdout_expected_cost=1000.0)
        first = promote_candidate_from_disk(db)
        assert first.promoted is True
        champion_version_id = first.model_version.id
        champion_policy_id = first.decision_policy.id

        # Same cost config, worse (higher) holdout expected_cost.
        _write_fake_artifacts(artifacts_root, "20990102_000000_v1", holdout_expected_cost=5000.0)
        second = promote_candidate_from_disk(db)

        assert second.promoted is False
        assert "worse" in second.reason
        assert second.model_version.status == "rejected_regression"

        # The original champion is untouched -- still champion, same active policy.
        still_champion = db.query(ModelVersion).filter(ModelVersion.id == champion_version_id).first()
        assert still_champion.status == "champion"
        still_active_policy = db.query(ModelDecisionPolicy).filter(ModelDecisionPolicy.id == champion_policy_id).first()
        assert still_active_policy.active is True
    finally:
        db.close()


def test_promote_accepts_better_candidate_and_retires_previous_champion(monkeypatch, tmp_path):
    from app.core.config import settings
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(artifacts_root))

    db = TestingSessionLocal()
    try:
        _write_fake_artifacts(artifacts_root, "20990101_000000_v1", holdout_expected_cost=5000.0)
        first = promote_candidate_from_disk(db)
        assert first.promoted is True
        old_champion_id = first.model_version.id
        old_policy_id = first.decision_policy.id

        # Same cost config, better (lower) holdout expected_cost.
        _write_fake_artifacts(artifacts_root, "20990102_000000_v1", holdout_expected_cost=1000.0)
        second = promote_candidate_from_disk(db)

        assert second.promoted is True
        assert second.model_version.id != old_champion_id
        assert second.model_version.status == "champion"
        assert second.decision_policy.active is True

        old_champion = db.query(ModelVersion).filter(ModelVersion.id == old_champion_id).first()
        assert old_champion.status == "retired"
        old_policy = db.query(ModelDecisionPolicy).filter(ModelDecisionPolicy.id == old_policy_id).first()
        assert old_policy.active is False
    finally:
        db.close()


def test_promote_rejects_candidate_with_no_holdout_evaluation_when_champion_exists(monkeypatch, tmp_path):
    from app.core.config import settings
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(artifacts_root))

    db = TestingSessionLocal()
    try:
        _write_fake_artifacts(artifacts_root, "20990101_000000_v1", holdout_expected_cost=1000.0)
        first = promote_candidate_from_disk(db)
        assert first.promoted is True

        # No holdout_expected_cost -- never ran evaluate_holdout_step15.py.
        _write_fake_artifacts(artifacts_root, "20990102_000000_v1")
        second = promote_candidate_from_disk(db)

        assert second.promoted is False
        assert "no step15 holdout evaluation" in second.reason
        assert second.model_version.status == "rejected_regression"
    finally:
        db.close()


def test_rollback_to_previous_champion_reactivates_old_policy(monkeypatch, tmp_path):
    from app.core.config import settings
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(artifacts_root))

    db = TestingSessionLocal()
    try:
        _write_fake_artifacts(artifacts_root, "20990101_000000_v1", t_accept=0.3, holdout_expected_cost=5000.0)
        first = promote_candidate_from_disk(db)
        first_id, first_policy_id = first.model_version.id, first.decision_policy.id

        _write_fake_artifacts(artifacts_root, "20990102_000000_v1", t_accept=0.6, holdout_expected_cost=1000.0)
        second = promote_candidate_from_disk(db)
        second_id, second_policy_id = second.model_version.id, second.decision_policy.id
        assert second_id != first_id

        champion, policy = rollback_to_previous_champion(db)

        assert champion.id == first_id
        assert champion.status == "champion"
        assert policy.id == first_policy_id
        assert policy.active is True
        assert float(policy.accept_threshold) == 0.3

        demoted = db.query(ModelVersion).filter(ModelVersion.id == second_id).first()
        assert demoted.status == "retired"
        demoted_policy = db.query(ModelDecisionPolicy).filter(ModelDecisionPolicy.id == second_policy_id).first()
        assert demoted_policy.active is False
    finally:
        db.close()
