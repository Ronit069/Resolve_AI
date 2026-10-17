"""
Tests for cross-machine model artifact distribution (object storage
fallback in champion_loader.py). A promoted champion's model.cbm/
calibrator.pkl are uploaded to object storage at a deterministic key;
load_champion_artifacts(db) only reaches for that key when the local
artifact_uri path isn't present on the current machine, so a model
promoted on one machine can still be loaded on another.

Uses the real (moto-backed) storage client -- same convention as the
rest of the suite, which assumes a moto S3 mock is already running.
"""

import json
import os
import pickle
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
import app.models.shared
import app.models.module_a
import app.models.module_b
import app.models.module_c
import app.models.module_d
import app.models.module_e
import app.models.module_g
import app.models.module_h
from app.models.module_f import ModelDecisionPolicy, ModelMetric, ModelVersion

from app.services.mlops.champion_loader import (
    ChampionArtifactsUnavailable,
    load_champion_artifacts,
    upload_champion_artifacts_to_object_storage,
)
from app.services.mlops.model_registry import promote_candidate_from_disk

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_champion_artifact_object_storage.db"
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
        db.query(ModelMetric).delete()
        db.query(ModelDecisionPolicy).delete()
        db.query(ModelVersion).delete()
        db.commit()
    finally:
        db.close()


def _write_fake_artifacts(artifacts_root, run_id):
    import catboost
    from sklearn.isotonic import IsotonicRegression

    from app.services.ml.training.preprocessor import Preprocessor

    examples = []
    for i in range(20):
        label = i % 2
        examples.append({
            "features": {
                "reason_code": "10.4", "payment_method": "card",
                "amount_match": True, "order_id_match": True, "tracking_match": True,
                "timeline_valid": True, "refund_exists": False, "shipment_available": True,
                "required_evidence_coverage": 1.0 if label else 0.2,
                "missing_required_count": 0 if label else 3,
                "evidence_count": 3 if label else 0,
                "customer_match_score": 0.9, "days_delivery_to_dispute": 2.0,
                "contradiction_count": 0, "high_severity_contradictions": 0,
                "avg_ocr_confidence": 0.95, "min_ocr_confidence": 0.9,
                "document_quality_score": 0.9, "dispute_amount": 10000,
                "disputed_amount_ratio": 1.0, "days_to_deadline": 5.0,
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
        json.dump({"t_accept": 0.4, "t_contest": 0.7, "c_fp": 50.0, "c_fn": 100.0, "c_review": 5.0}, f)


@pytest.fixture
def fake_champion_artifacts_dir(tmp_path):
    artifacts_root = tmp_path / "artifacts"
    _write_fake_artifacts(artifacts_root, "20990101_000000_v1")
    return artifacts_root


def test_promotion_uploads_artifacts_to_object_storage(monkeypatch, fake_champion_artifacts_dir):
    from app.core.config import settings
    from app.core.storage import storage_client

    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(fake_champion_artifacts_dir))

    db = TestingSessionLocal()
    try:
        decision = promote_candidate_from_disk(db)
        assert decision.promoted is True

        model_bytes = storage_client.download_file(f"models/{decision.model_version.id}/model.cbm")
        calibrator_bytes = storage_client.download_file(f"models/{decision.model_version.id}/calibrator.pkl")
        assert len(model_bytes) > 0
        assert len(calibrator_bytes) > 0
    finally:
        db.close()


def test_load_champion_artifacts_falls_back_to_object_storage_when_local_files_missing(
    monkeypatch, fake_champion_artifacts_dir, tmp_path
):
    """Simulates a different machine: the champion is promoted here (uploading
    to object storage as a side effect), then the local artifact_uri path is
    deleted -- as it would simply never have existed on another machine --
    and load_champion_artifacts(db) must still succeed via the object-storage
    fallback, using a separate local cache directory."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(fake_champion_artifacts_dir))

    db = TestingSessionLocal()
    try:
        decision = promote_candidate_from_disk(db)
        assert decision.promoted is True
        model_version_id = decision.model_version.id

        local_model_path = decision.model_version.artifact_uri
        local_calibrator_path = decision.model_version.calibrator_uri
        assert os.path.isfile(local_model_path)

        # Simulate "a different machine": point MODEL_EVALUATION_ARTIFACT_DIR
        # (and therefore the object-storage cache dir) at a fresh empty
        # directory, and delete the original local artifact files outright.
        os.remove(local_model_path)
        os.remove(local_calibrator_path)
        other_machine_dir = tmp_path / "other_machine_cache_root"
        monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(other_machine_dir))

        artifacts = load_champion_artifacts(db)
        assert artifacts.model_version_id == model_version_id
        assert artifacts.model is not None
        assert artifacts.calibrator is not None

        # Cached locally under the new (empty) root, not re-downloaded on a
        # second call.
        cached_path = other_machine_dir / "_object_storage_cache" / "models" / str(model_version_id) / "model.cbm"
        assert cached_path.is_file()
    finally:
        db.close()


def test_load_champion_artifacts_raises_clearly_when_neither_local_nor_object_storage_has_it(monkeypatch):
    from app.core.config import settings

    db = TestingSessionLocal()
    try:
        # A ModelVersion whose artifact_uri points nowhere real, and that was
        # never uploaded to object storage either.
        bogus_id = uuid.uuid4()
        version = ModelVersion(
            id=bogus_id, algorithm="CatBoost", artifact_uri="/nonexistent/model.cbm",
            calibrator_uri="/nonexistent/calibrator.pkl", status="champion",
        )
        db.add(version)
        db.flush()
        db.add(ModelDecisionPolicy(
            model_version_id=bogus_id, accept_threshold=0.4, contest_threshold=0.7,
            fp_cost=50.0, fn_cost=100.0, review_cost=5.0, active=True,
        ))
        db.commit()

        with pytest.raises(ChampionArtifactsUnavailable):
            load_champion_artifacts(db)
    finally:
        db.close()


def test_upload_is_best_effort_and_never_raises_on_failure():
    # Build a candidate whose local paths don't exist, so the upload's
    # open(...) calls fail -- this must be swallowed, not raised.
    from app.services.mlops.champion_loader import ChampionArtifacts

    fake_candidate = ChampionArtifacts(
        model=None, calibrator=None, model_sha256="deadbeef", t_accept=0.4, t_contest=0.7,
        c_fp=50.0, c_fn=100.0, c_review=5.0, model_run_dir="", model_cbm_path="/nonexistent/model.cbm",
        calibrator_path="/nonexistent/calibrator.pkl", calibration_dir="", policy_dir="",
    )

    # Should not raise despite both paths being nonexistent.
    upload_champion_artifacts_to_object_storage(fake_candidate, uuid.uuid4())
