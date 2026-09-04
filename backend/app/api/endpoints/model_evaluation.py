"""
Module L — authoritative Step 15 held-out model evaluation endpoint.

Deliberately a separate router/file from H-22's queue-metrics
(observability.py) and L-04's runtime-metrics (runtime_observability.py)
— same read-only role-gate convention, different (previously unowned)
surface: the ONE authoritative offline holdout evaluation artifact
produced by evaluate_holdout_step15.py, never a live/tenant-scoped
metric and never recomputed here. If the artifact is missing this
returns 503, never fabricated zeros or placeholder metrics.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_merchant, require_role
from app.models.shared import Merchant, AppUser, AppUserRole
from app.schemas.module_l import (
    ModelEvaluationResponse,
    EvaluationConfusionMatrix,
    ModelEvaluationModelProvenance,
    ModelEvaluationProvenance,
)
from app.services.mlops.evaluation_artifact import load_latest_evaluation, EvaluationArtifactUnavailable

router = APIRouter()


@router.get("/model-evaluation", response_model=ModelEvaluationResponse)
def get_model_evaluation(
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
    current_user: AppUser = Depends(
        require_role([AppUserRole.MERCHANT_ADMIN, AppUserRole.RISK_ANALYST, AppUserRole.APPROVER])
    ),
):
    """
    Read-only exposure of the authoritative Step 15 held-out evaluation
    artifact (frozen model + validation-only calibration + validation-only
    locked policy, evaluated once against the untouched test_holdout
    split). Not tenant-scoped — this is a single global model-evaluation
    artifact, not per-merchant data; current_merchant is required only to
    preserve the existing authenticated-request convention used by every
    other observability endpoint.
    """
    try:
        data = load_latest_evaluation()
    except EvaluationArtifactUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

    metrics = data["metrics"]
    cm = metrics["confusion_matrix"]
    holdout = data["test_holdout_dataset"]
    champion = data["champion_model"]
    policy = data["decision_policy"]

    return ModelEvaluationResponse(
        sample_count=holdout["example_count"],
        positive_count=holdout["positive_count"],
        negative_count=holdout["negative_count"],
        precision=metrics["precision"],
        recall=metrics["recall"],
        f1=metrics["f1"],
        accuracy=metrics["accuracy"],
        confusion_matrix=EvaluationConfusionMatrix(**cm),
        false_positive_count=cm["fp"],
        expected_cost=metrics["expected_cost"],
        accept_count=metrics["accept_count"],
        review_count=metrics["review_count"],
        contest_count=metrics["contest_count"],
        brier_raw=metrics["brier_score_raw"],
        brier_calibrated=metrics["brier_score_calibrated"],
        model=ModelEvaluationModelProvenance(
            algorithm=champion["algorithm"],
            run_id=champion["run_dir"],
            model_sha256=champion["model_cbm_sha256"],
        ),
        evaluation=ModelEvaluationProvenance(
            holdout_file=holdout["file"],
            holdout_sha256=holdout["sha256"],
            evaluation_timestamp=data["timestamp"],
            calibration_method=data["calibration"].get("calibration_method"),
            policy_id=policy["step14_dir"],
        ),
    )
