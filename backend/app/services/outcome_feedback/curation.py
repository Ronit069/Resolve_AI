"""
Module K — curation action.

Separate, explicitly authorized (MODEL_MAINTAINER only) path from outcome
webhook ingestion. Only this path may ever set
CuratedFeedbackLabel.approved_for_training=True — the webhook ingestion
path (outcome_ingestion.py) never constructs a CuratedFeedbackLabel at all.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.module_h import CuratedFeedbackLabel, DisputeOutcome, LabelQuality


class CurationOutcomeNotFound(Exception):
    def __init__(self, outcome_id: UUID):
        self.outcome_id = outcome_id
        super().__init__(f"No DisputeOutcome found for outcome_id={outcome_id}")


def curate_outcome(
    db: Session,
    outcome_id: UUID,
    label_name: str,
    label_value: str,
    label_quality: LabelQuality,
    curated_by: UUID,
    approved_for_training: bool = False,
) -> CuratedFeedbackLabel:
    """
    Always inserts a new CuratedFeedbackLabel row (never updates an
    existing one, mirroring DisputeOutcome's append-only discipline).
    version is the next version for this (outcome_id, label_name) pair.
    """
    outcome = (
        db.query(DisputeOutcome)
        .filter(DisputeOutcome.id == outcome_id)
        .populate_existing()
        .first()
    )
    if outcome is None:
        raise CurationOutcomeNotFound(outcome_id)

    latest = (
        db.query(CuratedFeedbackLabel)
        .filter(
            CuratedFeedbackLabel.outcome_id == outcome_id,
            CuratedFeedbackLabel.label_name == label_name,
        )
        .order_by(CuratedFeedbackLabel.version.desc())
        .populate_existing()
        .first()
    )
    next_version = (latest.version + 1) if latest is not None else 1

    label = CuratedFeedbackLabel(
        case_id=outcome.case_id,
        outcome_id=outcome.id,
        label_name=label_name,
        label_value=label_value,
        label_quality=label_quality,
        curated_by=curated_by,
        version=next_version,
        approved_for_training=approved_for_training,
    )
    db.add(label)
    db.commit()
    return label
