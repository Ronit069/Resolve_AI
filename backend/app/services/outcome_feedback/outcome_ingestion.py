"""
Module K — outcome webhook ingestion.

Dedicated ingestion path for Razorpay's terminal dispute-outcome events
(won/lost/closed). Deliberately separate from Module A's
process_dispute_event (app/services/ingestion.py) — a different event
category (outcome vs. dispute-status) — and Module A is frozen and
unmodified by this module.

Idempotency is enforced purely via the DB-level UNIQUE constraint on
DisputeOutcome.source_event_id (sourced from the X-Razorpay-Event-Id
header, never from the JSON body) — no separate WebhookEvent row is
written for this event category.
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.module_a import Dispute
from app.models.module_f import RiskPrediction
from app.models.module_h import ContestPackage, ContestSubmission, DisputeOutcome, DisputeOutcomeEnum, SubmissionStatus

logger = logging.getLogger(__name__)

# Frozen Product Owner decision: only these three terminal outcome events
# are handled here. under_review remains Module A's concern (H-16 Clause B
# already satisfied there); anything else is not an outcome this module acts on.
EVENT_TYPE_TO_OUTCOME = {
    "payment.dispute.won": DisputeOutcomeEnum.WON,
    "payment.dispute.lost": DisputeOutcomeEnum.LOST,
    "payment.dispute.closed": DisputeOutcomeEnum.CLOSED,
}


class OutcomeCaseUnresolvable(Exception):
    """
    Raised when neither the ContestSubmission chain nor a Module A Dispute
    record resolves to a case_id for this Razorpay dispute ID.
    DisputeOutcome.case_id is NOT NULL in the frozen H-00 schema, so no row
    can be written without fabricating a relationship — the caller decides
    how to respond (never by inventing a case_id).
    """
    def __init__(self, razorpay_dispute_id: str):
        self.razorpay_dispute_id = razorpay_dispute_id
        super().__init__(
            f"No case could be resolved for Razorpay dispute id={razorpay_dispute_id}: "
            "no matching ContestSubmission and no ingested Dispute record. "
            "DisputeOutcome.case_id is NOT NULL; refusing to fabricate a relationship."
        )


def _resolve_contest_submission(db: Session, razorpay_dispute_id: str) -> Optional[ContestSubmission]:
    return (
        db.query(ContestSubmission)
        .filter(
            ContestSubmission.external_dispute_id == razorpay_dispute_id,
            ContestSubmission.action == "submit",
            ContestSubmission.status == SubmissionStatus.SUCCESS,
        )
        .order_by(ContestSubmission.submitted_at.desc())
        .populate_existing()
        .first()
    )


def _resolve_case_id_via_dispute(db: Session, razorpay_dispute_id: str) -> Optional[UUID]:
    """
    Fallback used only when no ContestSubmission matches. Uses Module A's
    already-canonical external_dispute_id -> case_id mapping, which exists
    independently of whether Module J ever submitted a contest — this is
    not a fabricated relationship, it is the same mapping Module A itself
    already owns.
    """
    dispute = (
        db.query(Dispute)
        .filter(Dispute.external_dispute_id == razorpay_dispute_id)
        .populate_existing()
        .first()
    )
    return dispute.case_id if dispute is not None else None


def ingest_outcome_event(
    db: Session,
    event_type: str,
    razorpay_dispute_id: str,
    amount_deducted_minor: Optional[int],
    source_event_id: str,
    occurred_at: datetime,
) -> Optional[DisputeOutcome]:
    """
    Returns the created DisputeOutcome, or None if event_type is not one of
    the three handled outcome events, or if source_event_id is a duplicate
    (DB-level UNIQUE constraint) — both are 2xx-worthy no-ops for the
    caller, not errors.

    Raises OutcomeCaseUnresolvable if no case can be resolved without
    fabrication.
    """
    outcome_enum = EVENT_TYPE_TO_OUTCOME.get(event_type)
    if outcome_enum is None:
        return None

    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    submission = _resolve_contest_submission(db, razorpay_dispute_id)

    if submission is not None:
        package = (
            db.query(ContestPackage)
            .filter(ContestPackage.id == submission.contest_package_id)
            .populate_existing()
            .first()
        )
        case_id = package.case_id if package is not None else None
        contest_submission_id = submission.id
        prediction_id = None
        if case_id is not None:
            prediction = (
                db.query(RiskPrediction)
                .filter(RiskPrediction.case_id == case_id)
                .order_by(RiskPrediction.created_at.desc())
                .populate_existing()
                .first()
            )
            prediction_id = prediction.id if prediction is not None else None
        if case_id is None:
            # Should not happen given ContestPackage.case_id is NOT NULL, but
            # never fabricate — fall back to the same Dispute-based
            # resolution as the unresolved-submission path below.
            case_id = _resolve_case_id_via_dispute(db, razorpay_dispute_id)
    else:
        contest_submission_id = None
        prediction_id = None
        case_id = _resolve_case_id_via_dispute(db, razorpay_dispute_id)

    if case_id is None:
        raise OutcomeCaseUnresolvable(razorpay_dispute_id)

    outcome = DisputeOutcome(
        case_id=case_id,
        prediction_id=prediction_id,
        contest_submission_id=contest_submission_id,
        outcome=outcome_enum,
        amount_deducted_minor=amount_deducted_minor,
        source_event_id=source_event_id,
        occurred_at=occurred_at,
    )
    db.add(outcome)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info(f"Duplicate outcome webhook event ignored (idempotency): {source_event_id}")
        return None
    return outcome
