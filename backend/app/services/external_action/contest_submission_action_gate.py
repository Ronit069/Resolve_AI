"""
H-10 Draft Before Submit — local contest-submission action gate.

Follows the same pure read-and-decide gate pattern established by H-06's
deadline_gate.py, H-07's contest_amount_gate.py, and H-08's
evidence_mapping_gate.py: no endpoint, no external HTTP call, no
persistence.

This function decides ONLY the local action value (draft vs submit),
using existing frozen H-03/H-04/H-05 review data. It does not call
Razorpay, does not write ContestSubmission/ContestPackage, and is not
itself H-10's full external behavior — actual transmission of
action=draft/action=submit and persistence of the external result remain
deferred until H-13/outbox and contest-package/review execution
infrastructure exist.
"""

import dataclasses
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.module_h import ReviewQueueItem, ReviewAction, QueueStatus, ReviewActionEnum


@dataclasses.dataclass(frozen=True)
class ContestSubmissionActionResult:
    submit_eligible: bool
    action: str  # "submit" | "draft" — the literal Razorpay action value this case currently supports
    reason: str
    case_id: UUID
    queue_item_id: Optional[UUID]
    queue_status: Optional[str]
    finalizing_review_action_id: Optional[UUID]
    finalizing_review_action: Optional[str]
    checked_at: datetime


def determine_contest_submission_action(
    db: Session,
    case_id: UUID,
    current_time: Optional[datetime] = None,
) -> ContestSubmissionActionResult:
    """
    H-10 local decision predicate.

    A case is submit-eligible only when its most recent ReviewQueueItem is
    DONE and the most recent ReviewAction recorded against it is a
    finalized APPROVE_CONTEST. Every other state or outcome — not yet
    reviewed, PENDING_SECOND_APPROVAL (a first approval alone is never
    sufficient), or any DONE outcome other than APPROVE_CONTEST (including
    an ESCALATE that cancelled a pending H-05 dual-control decision) —
    resolves to "draft".

    Never accepts a caller-supplied queue status or review outcome; always
    performs a fresh read of the canonical ReviewQueueItem/ReviewAction
    rows for the case, bypassing any objects already loaded earlier in the
    request/session.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    queue_item = (
        db.query(ReviewQueueItem)
        .filter(ReviewQueueItem.case_id == case_id)
        .order_by(ReviewQueueItem.created_at.desc())
        .populate_existing()
        .first()
    )

    if queue_item is None:
        return ContestSubmissionActionResult(
            submit_eligible=False,
            action="draft",
            reason="No review queue item found for case",
            case_id=case_id,
            queue_item_id=None,
            queue_status=None,
            finalizing_review_action_id=None,
            finalizing_review_action=None,
            checked_at=current_time,
        )

    if queue_item.queue_status in (QueueStatus.PENDING, QueueStatus.ASSIGNED):
        return ContestSubmissionActionResult(
            submit_eligible=False,
            action="draft",
            reason=f"Queue item is {queue_item.queue_status.value}; review not yet finalized",
            case_id=case_id,
            queue_item_id=queue_item.id,
            queue_status=queue_item.queue_status.value,
            finalizing_review_action_id=None,
            finalizing_review_action=None,
            checked_at=current_time,
        )

    if queue_item.queue_status == QueueStatus.PENDING_SECOND_APPROVAL:
        return ContestSubmissionActionResult(
            submit_eligible=False,
            action="draft",
            reason="Pending second approval; first approval alone is not sufficient",
            case_id=case_id,
            queue_item_id=queue_item.id,
            queue_status=queue_item.queue_status.value,
            finalizing_review_action_id=None,
            finalizing_review_action=None,
            checked_at=current_time,
        )

    # queue_status == DONE: the most recent ReviewAction for this queue item
    # is always the one that finalized it, whether via a plain H-03
    # approval, the second approval in an H-05 dual-control sequence, or an
    # H-05 ESCALATE cancellation — every code path that sets queue_status
    # to DONE creates that exact ReviewAction in the same transaction.
    finalizing_action = (
        db.query(ReviewAction)
        .filter(ReviewAction.queue_item_id == queue_item.id)
        .order_by(ReviewAction.created_at.desc())
        .populate_existing()
        .first()
    )

    if finalizing_action is None:
        return ContestSubmissionActionResult(
            submit_eligible=False,
            action="draft",
            reason="Queue item is DONE but no review action found",
            case_id=case_id,
            queue_item_id=queue_item.id,
            queue_status=queue_item.queue_status.value,
            finalizing_review_action_id=None,
            finalizing_review_action=None,
            checked_at=current_time,
        )

    if finalizing_action.action == ReviewActionEnum.APPROVE_CONTEST:
        return ContestSubmissionActionResult(
            submit_eligible=True,
            action="submit",
            reason="Finalized APPROVE_CONTEST decision",
            case_id=case_id,
            queue_item_id=queue_item.id,
            queue_status=queue_item.queue_status.value,
            finalizing_review_action_id=finalizing_action.id,
            finalizing_review_action=finalizing_action.action.value,
            checked_at=current_time,
        )

    return ContestSubmissionActionResult(
        submit_eligible=False,
        action="draft",
        reason=f"Finalized decision was {finalizing_action.action.value}, not APPROVE_CONTEST",
        case_id=case_id,
        queue_item_id=queue_item.id,
        queue_status=queue_item.queue_status.value,
        finalizing_review_action_id=finalizing_action.id,
        finalizing_review_action=finalizing_action.action.value,
        checked_at=current_time,
    )
