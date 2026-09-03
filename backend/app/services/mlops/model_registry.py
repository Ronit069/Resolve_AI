"""
Module L — model registry (champion) convention.

Frozen Product Owner decision: NO database migration for a champion
flag. "Champion" is expressed purely through ModelVersion.status — an
existing free-text column (app/models/module_f.py) — never a new
column. This module only adds the convention/helpers around that
existing column; Module F's training/evaluation/calibration logic is
untouched.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.module_f import ModelVersion

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
