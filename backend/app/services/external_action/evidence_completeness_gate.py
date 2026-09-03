"""
H-11 Minimum Evidence For Submit — mandatory safety gate at the external-action boundary.

Follows the same pure read-and-decide gate pattern established by H-06's
deadline_gate.py, H-07's contest_amount_gate.py, and H-08's
evidence_mapping_gate.py: no endpoint, no external HTTP call, no
persistence.

H-11 enforces a real external Razorpay precondition confirmed during
Module J Category D contract research: a contest submission cannot
reference zero evidence documents. It is invoked only when H-10 has
already determined action == "submit" — a draft carries no such
requirement and never calls this gate.
"""

import dataclasses
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.module_h import ContestPackage, ContestPackageDocument

logger = logging.getLogger(__name__)


class EvidenceCompletenessGateErrorCode:
    EVIDENCE_MINIMUM_NOT_MET = "EVIDENCE_MINIMUM_NOT_MET"


@dataclasses.dataclass(frozen=True)
class H11GateResult:
    allowed: bool
    error_code: Optional[str]
    reason: Optional[str]
    case_id: UUID
    contest_package_id: UUID
    approved_document_count: Optional[int]
    checked_at: datetime


def check_minimum_evidence_for_submit(
    db: Session,
    case_id: UUID,
    contest_package_id: UUID,
    current_time: Optional[datetime] = None,
) -> H11GateResult:
    """
    H-11 mandatory safety gate.

    MUST be invoked immediately before any CONTEST_SUBMIT outbox write —
    i.e. only when H-10 has already determined action == "submit". A
    CONTEST_DRAFT write never calls this gate.

    Performs its own fresh read of the canonical ContestPackageDocument
    rows for the package rather than trusting a count computed earlier in
    the request/session. Any unexpected error while reading fails closed
    (allowed=False) rather than risking a submit built on an unverified
    evidence count.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    try:
        package = (
            db.query(ContestPackage)
            .filter(ContestPackage.id == contest_package_id, ContestPackage.case_id == case_id)
            .populate_existing()
            .first()
        )

        if package is None:
            return H11GateResult(
                allowed=False,
                error_code=EvidenceCompletenessGateErrorCode.EVIDENCE_MINIMUM_NOT_MET,
                reason="No contest package found for case",
                case_id=case_id,
                contest_package_id=contest_package_id,
                approved_document_count=None,
                checked_at=current_time,
            )

        approved_count = (
            db.query(ContestPackageDocument)
            .filter(
                ContestPackageDocument.contest_package_id == package.id,
                ContestPackageDocument.approved == True,
            )
            .count()
        )
    except Exception:
        logger.exception(
            "H-11 evidence completeness check failed unexpectedly for case_id=%s contest_package_id=%s",
            case_id, contest_package_id,
        )
        return H11GateResult(
            allowed=False,
            error_code=EvidenceCompletenessGateErrorCode.EVIDENCE_MINIMUM_NOT_MET,
            reason="Unexpected error while checking evidence completeness; failing closed",
            case_id=case_id,
            contest_package_id=contest_package_id,
            approved_document_count=None,
            checked_at=current_time,
        )

    if approved_count == 0:
        return H11GateResult(
            allowed=False,
            error_code=EvidenceCompletenessGateErrorCode.EVIDENCE_MINIMUM_NOT_MET,
            reason="Contest package has zero approved evidence documents; Razorpay requires at least one for submit",
            case_id=case_id,
            contest_package_id=contest_package_id,
            approved_document_count=0,
            checked_at=current_time,
        )

    return H11GateResult(
        allowed=True,
        error_code=None,
        reason=None,
        case_id=case_id,
        contest_package_id=contest_package_id,
        approved_document_count=approved_count,
        checked_at=current_time,
    )
