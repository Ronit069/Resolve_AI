"""
Module L — model registry (champion) convention.

Frozen Product Owner decision: NO database migration for a champion
flag. "Champion" is expressed purely through ModelVersion.status — an
existing free-text column (app/models/module_f.py) — never a new
column. This module only adds the convention/helpers around that
existing column; Module F's training/evaluation/calibration logic is
untouched.
"""

from typing import Optional, Tuple, TYPE_CHECKING
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.module_f import ModelVersion, ModelDecisionPolicy

if TYPE_CHECKING:
    from app.services.mlops.champion_loader import ChampionArtifacts

CHAMPION_STATUS = "champion"
RETIRED_STATUS = "retired"


def get_champion_model_version(db: Session, algorithm: Optional[str] = None) -> Optional[ModelVersion]:
    query = db.query(ModelVersion).filter(ModelVersion.status == CHAMPION_STATUS)
    if algorithm is not None:
        query = query.filter(ModelVersion.algorithm == algorithm)
    return query.order_by(ModelVersion.created_at.desc()).first()


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


def register_champion_from_artifacts(db: Session, artifacts: "ChampionArtifacts") -> Tuple[ModelVersion, ModelDecisionPolicy]:
    """
    Ensures a ModelVersion + active ModelDecisionPolicy exist in the DB
    that accurately describe the given on-disk champion artifacts
    (reusing mark_champion's existing champion-tracking convention
    above), so every live RiskPrediction references real, inspectable
    provenance -- not the demo seed's placeholder rows (seed_demo.py's
    ModelVersion/ModelDecisionPolicy have no artifact_uri/thresholds set
    at all; this is the first place those fields are ever populated with
    real values). Idempotent: returns the existing pair unchanged if one
    already points at this exact model file.
    """
    existing_version = (
        db.query(ModelVersion)
        .filter(ModelVersion.algorithm == "CatBoost", ModelVersion.artifact_uri == artifacts.model_cbm_path)
        .first()
    )
    if existing_version is not None:
        existing_policy = (
            db.query(ModelDecisionPolicy)
            .filter(
                ModelDecisionPolicy.model_version_id == existing_version.id,
                ModelDecisionPolicy.active == True,
            )
            .first()
        )
        if existing_policy is not None:
            return existing_version, existing_policy
        model_version = existing_version
    else:
        model_version = ModelVersion(
            algorithm="CatBoost",
            model_name="ResolveAI Champion CatBoost",
            feature_schema_version="ml_features_v1",
            artifact_uri=artifacts.model_cbm_path,
            preprocessing_hash=artifacts.model_sha256,
            calibrator_uri=artifacts.calibrator_path,
            status="pending",
        )
        db.add(model_version)
        db.flush()

    champion = mark_champion(db, model_version.id)

    db.query(ModelDecisionPolicy).filter(
        ModelDecisionPolicy.model_version_id == champion.id,
        ModelDecisionPolicy.active == True,
    ).update({"active": False})

    policy = ModelDecisionPolicy(
        model_version_id=champion.id,
        policy_version=artifacts.policy_dir,
        accept_threshold=artifacts.t_accept,
        contest_threshold=artifacts.t_contest,
        fp_cost=artifacts.c_fp,
        fn_cost=artifacts.c_fn,
        review_cost=artifacts.c_review,
        active=True,
    )
    db.add(policy)
    db.flush()
    return champion, policy
