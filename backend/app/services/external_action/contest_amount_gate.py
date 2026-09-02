"""
H-07 Contest Amount Validation — mandatory safety gate at the external-action boundary.

Follows the same pure read-and-decide gate pattern established by H-06's
deadline_gate.py: no endpoint, no external HTTP call, no persistence.

H-07 validates the candidate contest amount against the freshest local
canonical Dispute.amount_minor. H-07 does not define the upstream
mechanism that establishes that candidate as the human-approved final
amount.
"""

import dataclasses
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.module_a import Dispute


class ContestAmountGateErrorCode:
    CONTEST_AMOUNT_INVALID = "CONTEST_AMOUNT_INVALID"


@dataclasses.dataclass(frozen=True)
class ContestAmountGateResult:
    allowed: bool
    error_code: Optional[str]
    reason: Optional[str]
    candidate_contest_amount_minor: int
    dispute_amount_minor: Optional[int]
    is_full_contest: Optional[bool]
    checked_at: datetime


def validate_contest_amount(
    db: Session,
    case_id: UUID,
    contest_amount_minor: int,
    current_time: Optional[datetime] = None,
) -> ContestAmountGateResult:
    """
    H-07 mandatory safety gate.

    MUST be invoked immediately before any external Razorpay contest action,
    once the contest-package/external-action pipeline exists. Accepts
    contest_amount_minor as the candidate value under validation, but never
    accepts a caller-supplied dispute amount — the ceiling is always read
    fresh from the canonical Dispute row by case_id, bypassing any Dispute
    object already loaded earlier in the request/session.

    H-07 does not prove the candidate amount was human-approved; that
    upstream provenance is a future integration responsibility.
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
        return ContestAmountGateResult(
            allowed=False,
            error_code=ContestAmountGateErrorCode.CONTEST_AMOUNT_INVALID,
            reason="No dispute record found for case",
            candidate_contest_amount_minor=contest_amount_minor,
            dispute_amount_minor=None,
            is_full_contest=None,
            checked_at=current_time,
        )

    dispute_amount_minor = int(dispute.amount_minor)

    if contest_amount_minor <= 0:
        return ContestAmountGateResult(
            allowed=False,
            error_code=ContestAmountGateErrorCode.CONTEST_AMOUNT_INVALID,
            reason=f"contest_amount_minor {contest_amount_minor} is not positive",
            candidate_contest_amount_minor=contest_amount_minor,
            dispute_amount_minor=dispute_amount_minor,
            is_full_contest=None,
            checked_at=current_time,
        )

    if contest_amount_minor > dispute_amount_minor:
        return ContestAmountGateResult(
            allowed=False,
            error_code=ContestAmountGateErrorCode.CONTEST_AMOUNT_INVALID,
            reason=(
                f"contest_amount_minor {contest_amount_minor} exceeds "
                f"dispute_amount_minor {dispute_amount_minor}"
            ),
            candidate_contest_amount_minor=contest_amount_minor,
            dispute_amount_minor=dispute_amount_minor,
            is_full_contest=None,
            checked_at=current_time,
        )

    is_full_contest = contest_amount_minor == dispute_amount_minor

    return ContestAmountGateResult(
        allowed=True,
        error_code=None,
        reason=None,
        candidate_contest_amount_minor=contest_amount_minor,
        dispute_amount_minor=dispute_amount_minor,
        is_full_contest=is_full_contest,
        checked_at=current_time,
    )
