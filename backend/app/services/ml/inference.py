"""
Module F — live risk scoring.

Scores a case's current CaseFeatureSnapshot with the frozen champion
model + calibrator + locked 3-way decision policy already produced by
the offline pipeline (see mlops/champion_loader.py), and persists the
result as a RiskPrediction. This is the only place in the application
that calls model.predict_proba on live traffic -- everything upstream
(feature_snapshot.py) and downstream (review/queue.py) is unchanged.

Never fabricates a prediction: if there is no current feature snapshot,
or no champion artifacts are available on this machine, score_case()
returns None and the caller leaves the case exactly where it was.
"""

import logging
import uuid
from typing import Dict, Optional

import numpy as np
from sqlalchemy.orm import Session

from app.models.module_e import CaseFeatureSnapshot
from app.models.module_f import RiskPrediction
from app.models.shared import Case
from app.services.ml.training.preprocessor import Preprocessor
from app.services.mlops.champion_loader import ChampionArtifactsUnavailable, load_champion_artifacts
from app.services.mlops.model_registry import register_champion_from_artifacts

logger = logging.getLogger(__name__)


# ENGINEERING INFERENCE: hard_block has no separate specification for
# live inference. It mirrors the same deterministic, pre-prediction
# signals already present in the feature snapshot -- missing mandatory
# evidence, a fatal (high-severity) contradiction, or an already-expired
# deadline -- any of which should force human review regardless of the
# model's confidence. This is a live-inference-only rule: it never reads
# label_policy.py's training label logic.
def _compute_hard_block(features: Dict) -> bool:
    if features.get("missing_required_count", 0) > 0:
        return True
    if features.get("high_severity_contradictions", 0) > 0:
        return True
    days_to_deadline = features.get("days_to_deadline")
    if days_to_deadline is not None and days_to_deadline < 0:
        return True
    return False


def score_case(db: Session, case_id: uuid.UUID) -> Optional[RiskPrediction]:
    """
    Scores case_id's current CaseFeatureSnapshot. Returns the existing
    RiskPrediction without rescoring if one already exists for that
    snapshot (idempotent). Returns None -- a clean no-op, not an error --
    if the case has no current feature snapshot yet, or the champion
    artifacts aren't available on this machine.
    """
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        return None

    snapshot = (
        db.query(CaseFeatureSnapshot)
        .filter(CaseFeatureSnapshot.case_id == case_id, CaseFeatureSnapshot.is_current == True)
        .order_by(CaseFeatureSnapshot.created_at.desc())
        .first()
    )
    if not snapshot:
        return None

    idempotency_key = f"pred_{snapshot.id}"
    existing = db.query(RiskPrediction).filter(RiskPrediction.idempotency_key == idempotency_key).first()
    if existing:
        return existing

    try:
        artifacts = load_champion_artifacts()
    except ChampionArtifactsUnavailable as exc:
        logger.warning(f"Risk scoring skipped for case {case_id}: {exc}")
        return None

    model_version, decision_policy = register_champion_from_artifacts(db, artifacts)

    preprocessor = Preprocessor()
    example = [{"features": snapshot.features_json, "label": 0}]
    preprocessor.fit(example)
    X, _ = preprocessor.transform(example)

    raw_score = float(artifacts.model.predict_proba(X)[:, 1][0])
    calibrated_probability = float(np.clip(artifacts.calibrator.predict([raw_score])[0], 0.0, 1.0))

    if calibrated_probability < artifacts.t_accept:
        recommendation = "ACCEPT"
    elif calibrated_probability < artifacts.t_contest:
        recommendation = "REVIEW"
    else:
        recommendation = "CONTEST"

    hard_block = _compute_hard_block(snapshot.features_json)

    prediction = RiskPrediction(
        case_id=case_id,
        feature_snapshot_id=snapshot.id,
        model_version_id=model_version.id,
        decision_policy_id=decision_policy.id,
        raw_score=raw_score,
        calibrated_probability=calibrated_probability,
        recommendation=recommendation,
        hard_block=hard_block,
        idempotency_key=idempotency_key,
    )
    db.add(prediction)
    db.flush()
    return prediction
