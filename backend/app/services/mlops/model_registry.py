"""
Module L — model registry (champion) convention.

Frozen Product Owner decision: NO database migration for a champion
flag. "Champion" is expressed purely through ModelVersion.status — an
existing free-text column (app/models/module_f.py) — never a new
column. Holdout metrics used by the promotion guardrail below reuse
the existing ModelMetric table the same way, instead of adding columns
to ModelVersion/ModelDecisionPolicy.
"""

from dataclasses import dataclass
from typing import Optional, Tuple, TYPE_CHECKING
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.module_f import ModelDecisionPolicy, ModelMetric, ModelVersion

if TYPE_CHECKING:
    from app.services.mlops.champion_loader import ChampionArtifacts

CHAMPION_STATUS = "champion"
RETIRED_STATUS = "retired"
PENDING_EVALUATION_STATUS = "pending_evaluation"
REJECTED_REGRESSION_STATUS = "rejected_regression"

HOLDOUT_SPLIT = "test_holdout"
HOLDOUT_METRIC_NAMES = ("expected_cost", "precision", "recall", "f1", "brier_score_calibrated")


def get_champion_model_version(db: Session, algorithm: Optional[str] = None) -> Optional[ModelVersion]:
    query = db.query(ModelVersion).filter(ModelVersion.status == CHAMPION_STATUS)
    if algorithm is not None:
        query = query.filter(ModelVersion.algorithm == algorithm)
    return query.order_by(ModelVersion.created_at.desc()).first()


def get_active_policy(db: Session, model_version_id: UUID) -> Optional[ModelDecisionPolicy]:
    return (
        db.query(ModelDecisionPolicy)
        .filter(ModelDecisionPolicy.model_version_id == model_version_id, ModelDecisionPolicy.active == True)
        .first()
    )


def mark_champion(db: Session, model_version_id: UUID) -> ModelVersion:
    """
    Marks the given ModelVersion as champion, demoting any previously
    champion ModelVersion of the SAME algorithm to "retired" (status is
    free text; this is a convention, not a DB constraint). Champions of
    other algorithms are untouched — each algorithm may have its own
    champion at the same time.
    """
    target = db.query(ModelVersion).filter(ModelVersion.id == model_version_id).first()
    if target is None:
        raise ValueError(f"No ModelVersion found for id={model_version_id}")

    previous_champions = (
        db.query(ModelVersion)
        .filter(ModelVersion.algorithm == target.algorithm, ModelVersion.status == CHAMPION_STATUS)
        .all()
    )
    for previous in previous_champions:
        if previous.id != target.id:
            previous.status = RETIRED_STATUS

    target.status = CHAMPION_STATUS
    db.commit()
    db.refresh(target)
    return target


def _record_holdout_metrics(db: Session, model_version_id: UUID, holdout_metrics: dict) -> None:
    for name in HOLDOUT_METRIC_NAMES:
        value = holdout_metrics.get(name)
        if value is None:
            continue
        db.add(ModelMetric(
            model_version_id=model_version_id,
            split=HOLDOUT_SPLIT,
            metric_name=name,
            metric_value=value,
            metric_context={"holdout_eval_dir": holdout_metrics.get("holdout_eval_dir")},
        ))
    db.flush()


def get_holdout_metric(db: Session, model_version_id: UUID, metric_name: str) -> Optional[float]:
    row = (
        db.query(ModelMetric)
        .filter(
            ModelMetric.model_version_id == model_version_id,
            ModelMetric.split == HOLDOUT_SPLIT,
            ModelMetric.metric_name == metric_name,
        )
        .order_by(ModelMetric.id.desc())
        .first()
    )
    return float(row.metric_value) if row is not None else None


@dataclass
class PromotionDecision:
    promoted: bool
    reason: str
    model_version: ModelVersion
    decision_policy: Optional[ModelDecisionPolicy]


def _evaluate_candidate(
    db: Session, candidate: "ChampionArtifacts", current_champion: Optional[ModelVersion]
) -> Tuple[bool, str]:
    if current_champion is None:
        return True, "No existing champion for this algorithm -- promoting as the first champion."

    champion_cost = get_holdout_metric(db, current_champion.id, "expected_cost")
    if champion_cost is None:
        return True, (
            "Existing champion has no recorded holdout expected_cost to compare against "
            "(predates this guardrail, or was promoted without a step15 evaluation) -- "
            "promoting candidate."
        )

    if candidate.holdout_metrics is None or candidate.holdout_metrics.get("expected_cost") is None:
        return False, (
            "Candidate has no step15 holdout evaluation on disk. Run evaluate_holdout_step15.py "
            "before promoting -- an unevaluated model is never auto-promoted over an existing champion."
        )

    champion_policy = get_active_policy(db, current_champion.id)
    same_cost_config = (
        champion_policy is not None
        and float(champion_policy.fp_cost) == candidate.c_fp
        and float(champion_policy.fn_cost) == candidate.c_fn
        and float(champion_policy.review_cost) == candidate.c_review
    )
    if not same_cost_config:
        return False, (
            "Candidate's cost configuration (c_fp/c_fn/c_review) differs from the current "
            "champion's -- holdout expected_cost values aren't comparable under the same business "
            "costs. Promote manually (mark_champion) if this change is intentional."
        )

    candidate_cost = candidate.holdout_metrics["expected_cost"]
    if candidate_cost <= champion_cost:
        return True, (
            f"Candidate holdout expected_cost ({candidate_cost}) is <= current champion's "
            f"({champion_cost}) -- promoting."
        )
    return False, (
        f"Candidate holdout expected_cost ({candidate_cost}) is worse than current champion's "
        f"({champion_cost}) -- keeping existing champion."
    )


def promote_candidate_from_disk(db: Session) -> PromotionDecision:
    """
    The guardrail: loads the latest model/calibrator/policy on disk
    (load_candidate_artifacts_from_disk) and promotes it to champion
    ONLY if it is at least as good as the current champion's recorded
    holdout performance under the same cost configuration -- or there
    is no existing champion yet. A rejected candidate is still
    registered (status=rejected_regression / pending_evaluation) for
    auditability, but the existing champion keeps serving live traffic.

    Raises ChampionArtifactsUnavailable if there is no candidate on
    disk at all (nothing to evaluate).
    """
    from app.services.mlops.champion_loader import load_candidate_artifacts_from_disk

    candidate = load_candidate_artifacts_from_disk()

    existing_version = (
        db.query(ModelVersion)
        .filter(ModelVersion.algorithm == "CatBoost", ModelVersion.artifact_uri == candidate.model_cbm_path)
        .first()
    )
    if existing_version is not None and existing_version.status == CHAMPION_STATUS:
        return PromotionDecision(
            True, "Already the active champion.", existing_version, get_active_policy(db, existing_version.id)
        )

    current_champion = get_champion_model_version(db, algorithm="CatBoost")
    should_promote, reason = _evaluate_candidate(db, candidate, current_champion)

    if existing_version is not None:
        model_version = existing_version
    else:
        model_version = ModelVersion(
            algorithm="CatBoost",
            model_name="ResolveAI Champion CatBoost",
            feature_schema_version="ml_features_v1",
            artifact_uri=candidate.model_cbm_path,
            preprocessing_hash=candidate.model_sha256,
            calibrator_uri=candidate.calibrator_path,
            status=PENDING_EVALUATION_STATUS,
        )
        db.add(model_version)
        db.flush()

    if candidate.holdout_metrics is not None:
        _record_holdout_metrics(db, model_version.id, candidate.holdout_metrics)

    if not should_promote:
        model_version.status = REJECTED_REGRESSION_STATUS if current_champion is not None else PENDING_EVALUATION_STATUS
        db.commit()
        db.refresh(model_version)
        return PromotionDecision(False, reason, model_version, None)

    champion = mark_champion(db, model_version.id)

    if current_champion is not None and current_champion.id != champion.id:
        db.query(ModelDecisionPolicy).filter(
            ModelDecisionPolicy.model_version_id == current_champion.id,
            ModelDecisionPolicy.active == True,
        ).update({"active": False})

    policy = ModelDecisionPolicy(
        model_version_id=champion.id,
        policy_version=candidate.policy_dir,
        accept_threshold=candidate.t_accept,
        contest_threshold=candidate.t_contest,
        fp_cost=candidate.c_fp,
        fn_cost=candidate.c_fn,
        review_cost=candidate.c_review,
        active=True,
    )
    db.add(policy)
    db.commit()
    db.refresh(champion)
    db.refresh(policy)
    return PromotionDecision(True, reason, champion, policy)


def rollback_to_previous_champion(db: Session, algorithm: str = "CatBoost") -> Tuple[ModelVersion, ModelDecisionPolicy]:
    """
    Reverts to the most recently-created retired ModelVersion of this
    algorithm, re-activating its most recent ModelDecisionPolicy. Use
    after a promoted champion turns out bad in a way the offline
    holdout metrics didn't catch -- the guardrail in
    promote_candidate_from_disk only protects against a regression
    visible in those metrics, not one that only shows up live.

    Assumes champions are retired in the same order they were created
    (true for the normal challenger-vs-champion flow this registry
    supports); there is no separate "retired_at" column to order by
    without a migration.
    """
    old_champion = get_champion_model_version(db, algorithm=algorithm)

    previous = (
        db.query(ModelVersion)
        .filter(ModelVersion.algorithm == algorithm, ModelVersion.status == RETIRED_STATUS)
        .order_by(ModelVersion.created_at.desc())
        .first()
    )
    if previous is None:
        raise ValueError(f"No retired {algorithm} ModelVersion to roll back to.")

    previous_policy = (
        db.query(ModelDecisionPolicy)
        .filter(ModelDecisionPolicy.model_version_id == previous.id)
        .order_by(ModelDecisionPolicy.id.desc())
        .first()
    )
    if previous_policy is None:
        raise ValueError(f"Retired ModelVersion {previous.id} has no ModelDecisionPolicy to reactivate.")

    champion = mark_champion(db, previous.id)

    if old_champion is not None and old_champion.id != champion.id:
        db.query(ModelDecisionPolicy).filter(
            ModelDecisionPolicy.model_version_id == old_champion.id,
            ModelDecisionPolicy.active == True,
        ).update({"active": False})

    previous_policy.active = True
    db.commit()
    db.refresh(champion)
    db.refresh(previous_policy)
    return champion, previous_policy
