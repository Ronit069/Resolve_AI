"""
H-06 Deadline Recheck — mandatory safety gate at the external-action boundary.

This module owns H-06's live-serving actionability predicate independently of
Module F's `app/services/ml/label_policy.py`, which is frozen offline
dataset-labeling logic and must never be imported or called from here.

H-06's live safety predicate is implemented against the freshest local
canonical Dispute state. Authoritative Razorpay re-fetch remains a future
integration seam because the current repository contains no documented
outbound dispute-status API contract.

H-06 forces a fresh database-backed read rather than trusting a previously
loaded ORM instance. Authoritative external-provider re-fetch and stronger
transaction-level concurrency guarantees remain outside the MVP scope.
"""

import dataclasses
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.module_a import Dispute

# Frozen Product Owner decision: statuses that block any external action,
# mirrored (not imported) from Module F's offline label_policy.py precedent.
NON_ACTIONABLE_STATUSES = frozenset({"won", "lost", "closed", "expired", "withdrawn"})

# Frozen Product Owner decision: the only statuses recognized as actionable.
# Anything else — including None/empty/unrecognized values — fails closed.
KNOWN_ACTIONABLE_STATUSES = frozenset({"open", "under_review"})


class DeadlineGateErrorCode:
    CONTEST_DEADLINE_EXPIRED = "CONTEST_DEADLINE_EXPIRED"
    CONTEST_INVALID_STATUS = "CONTEST_INVALID_STATUS"


@dataclasses.dataclass(frozen=True)
class DeadlineGateResult:
    allowed: bool
    error_code: Optional[str]
    reason: Optional[str]
    dispute_status: Optional[str]
    respond_by: Optional[datetime]
    checked_at: datetime


def check_dispute_actionable(
    db: Session,
    case_id: UUID,
    current_time: Optional[datetime] = None,
) -> DeadlineGateResult:
    """
    H-06 mandatory safety gate.

    MUST be invoked immediately before any external Razorpay action (contest
    draft/submit, accept, or a document upload tied to a submission) once
    that pipeline exists. Takes only case_id — never accepts dispute status
    or respond_by from a caller, so a client can never override what gets
    checked. Performs its own fresh read of the canonical Dispute row rather
    than trusting a Dispute object loaded earlier in the request/session.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    dispute = (
        db.query(Dispute)
        .filter(Dispute.case_id == case_id)
        .populate_existing()
        .first()
    )

    if dispute is None:
        return DeadlineGateResult(
            allowed=False,
            error_code=DeadlineGateErrorCode.CONTEST_INVALID_STATUS,
            reason="No dispute record found for case",
            dispute_status=None,
            respond_by=None,
            checked_at=current_time,
        )

    raw_status = dispute.status
    status = (raw_status or "").strip().lower()

    if status in NON_ACTIONABLE_STATUSES:
        return DeadlineGateResult(
            allowed=False,
            error_code=DeadlineGateErrorCode.CONTEST_INVALID_STATUS,
            reason=f"Dispute status '{raw_status}' is non-actionable",
            dispute_status=raw_status,
            respond_by=dispute.respond_by,
            checked_at=current_time,
        )

    if status not in KNOWN_ACTIONABLE_STATUSES:
        return DeadlineGateResult(
            allowed=False,
            error_code=DeadlineGateErrorCode.CONTEST_INVALID_STATUS,
            reason=f"Dispute status '{raw_status}' is not a recognized actionable status",
            dispute_status=raw_status,
            respond_by=dispute.respond_by,
            checked_at=current_time,
        )

    respond_by = dispute.respond_by

    if respond_by is None:
        # Frozen Product Owner decision: a missing deadline cannot be
        # confirmed as not-yet-expired, so it fails closed.
        return DeadlineGateResult(
            allowed=False,
            error_code=DeadlineGateErrorCode.CONTEST_DEADLINE_EXPIRED,
            reason="respond_by is missing; cannot confirm dispute is still actionable",
            dispute_status=raw_status,
            respond_by=None,
            checked_at=current_time,
        )

    if respond_by.tzinfo is None:
        respond_by = respond_by.replace(tzinfo=timezone.utc)

    if respond_by <= current_time:
        return DeadlineGateResult(
            allowed=False,
            error_code=DeadlineGateErrorCode.CONTEST_DEADLINE_EXPIRED,
            reason="respond_by has passed",
            dispute_status=raw_status,
            respond_by=dispute.respond_by,
            checked_at=current_time,
        )

    return DeadlineGateResult(
        allowed=True,
        error_code=None,
        reason=None,
        dispute_status=raw_status,
        respond_by=dispute.respond_by,
        checked_at=current_time,
    )
