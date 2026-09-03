"""
H-08 Evidence Mapping — mandatory safety gate at the external-action boundary.

Follows the same pure read-and-decide gate pattern established by H-06's
deadline_gate.py and H-07's contest_amount_gate.py: no endpoint, no
external HTTP call, no persistence.

H-08 maps internal evidence types to Razorpay evidence fields and
establishes document safety/eligibility only. It does not establish or
claim human/reviewer approval — ContestPackageDocument.approved is a
separate, later workflow concern and is never read here. The actual
Razorpay document upload (H-09) is out of scope for this gate.
"""

import dataclasses
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.module_c import EvidenceType, EvidenceDocument, ScanStatus


class EvidenceGateErrorCode:
    EVIDENCE_MAPPING_INVALID = "EVIDENCE_MAPPING_INVALID"
    EVIDENCE_DOCUMENT_UNSAFE = "EVIDENCE_DOCUMENT_UNSAFE"


# Frozen Product Owner decision: only these five internal evidence types map
# to a Razorpay evidence field for the H-08 MVP. INVOICE, COURIER_TRACKING,
# and OTHER are explicitly left unmapped — never guessed or substituted.
EVIDENCE_TYPE_TO_RAZORPAY_FIELD = {
    EvidenceType.PROOF_OF_DELIVERY: "shipping_proof",
    EvidenceType.CUSTOMER_COMMUNICATION: "customer_communication",
    EvidenceType.SERVICE_CONFIRMATION: "proof_of_service",
    EvidenceType.TERMS_ACCEPTANCE: "term_and_conditions",
    EvidenceType.REFUND_RECEIPT: "refund_confirmation",
}


@dataclasses.dataclass(frozen=True)
class EvidenceMappingResult:
    mapped: bool
    error_code: Optional[str]
    reason: Optional[str]
    evidence_type: EvidenceType
    razorpay_evidence_field: Optional[str]


def map_evidence_type_to_razorpay_field(evidence_type: EvidenceType) -> EvidenceMappingResult:
    """
    Pure lookup — no DB access. An internal evidence type with no frozen
    mapping returns EVIDENCE_MAPPING_INVALID; it is never silently dropped
    nor substituted with a vaguely related Razorpay field.
    """
    field = EVIDENCE_TYPE_TO_RAZORPAY_FIELD.get(evidence_type)

    if field is None:
        return EvidenceMappingResult(
            mapped=False,
            error_code=EvidenceGateErrorCode.EVIDENCE_MAPPING_INVALID,
            reason=f"No Razorpay evidence field mapping exists for '{evidence_type.value}'",
            evidence_type=evidence_type,
            razorpay_evidence_field=None,
        )

    return EvidenceMappingResult(
        mapped=True,
        error_code=None,
        reason=None,
        evidence_type=evidence_type,
        razorpay_evidence_field=field,
    )


@dataclasses.dataclass(frozen=True)
class DocumentSafetyResult:
    safe: bool
    error_code: Optional[str]
    reason: Optional[str]
    case_id: UUID
    document_id: UUID
    scan_status: Optional[str]
    checked_at: datetime


def is_document_safe_for_contest(
    db: Session,
    case_id: UUID,
    document_id: UUID,
    current_time: Optional[datetime] = None,
) -> DocumentSafetyResult:
    """
    H-08 safety gate. Establishes document safety/eligibility only — it does
    not establish or claim human/reviewer approval.

    Requires both case_id and document_id. The two are filtered together in
    a single query, so a document belonging to a different case is
    indistinguishable from a missing document: it is never fetched or
    evaluated, and fails closed as EVIDENCE_DOCUMENT_UNSAFE.

    Performs its own fresh read of the canonical EvidenceDocument row rather
    than trusting an object loaded earlier in the request/session.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    document = (
        db.query(EvidenceDocument)
        .filter(
            EvidenceDocument.document_id == document_id,
            EvidenceDocument.case_id == case_id,
        )
        .populate_existing()
        .first()
    )

    if document is None:
        return DocumentSafetyResult(
            safe=False,
            error_code=EvidenceGateErrorCode.EVIDENCE_DOCUMENT_UNSAFE,
            reason="No evidence document found for the specified case",
            case_id=case_id,
            document_id=document_id,
            scan_status=None,
            checked_at=current_time,
        )

    if document.scan_status != ScanStatus.CLEAN:
        return DocumentSafetyResult(
            safe=False,
            error_code=EvidenceGateErrorCode.EVIDENCE_DOCUMENT_UNSAFE,
            reason=f"Document scan_status '{document.scan_status.value}' is not CLEAN",
            case_id=case_id,
            document_id=document_id,
            scan_status=document.scan_status.value,
            checked_at=current_time,
        )

    return DocumentSafetyResult(
        safe=True,
        error_code=None,
        reason=None,
        case_id=case_id,
        document_id=document_id,
        scan_status=document.scan_status.value,
        checked_at=current_time,
    )


@dataclasses.dataclass(frozen=True)
class EvidenceEligibilityResult:
    eligible: bool
    error_code: Optional[str]
    reason: Optional[str]
    mapping: EvidenceMappingResult
    safety: DocumentSafetyResult


def evaluate_evidence_for_contest(
    db: Session,
    case_id: UUID,
    document_id: UUID,
    evidence_type: EvidenceType,
    current_time: Optional[datetime] = None,
) -> EvidenceEligibilityResult:
    """
    Convenience composition of the two checks above.

    Ordering (implementation detail, not a source-level business
    requirement): document safety is evaluated first; if unsafe, the result
    surfaces EVIDENCE_DOCUMENT_UNSAFE without evaluating the mapping.
    Otherwise, evidence-type mapping is evaluated; if unmapped, the result
    surfaces EVIDENCE_MAPPING_INVALID. Only mapped + safe is eligible.
    """
    safety = is_document_safe_for_contest(db, case_id, document_id, current_time=current_time)

    if not safety.safe:
        mapping = map_evidence_type_to_razorpay_field(evidence_type)
        return EvidenceEligibilityResult(
            eligible=False,
            error_code=safety.error_code,
            reason=safety.reason,
            mapping=mapping,
            safety=safety,
        )

    mapping = map_evidence_type_to_razorpay_field(evidence_type)

    if not mapping.mapped:
        return EvidenceEligibilityResult(
            eligible=False,
            error_code=mapping.error_code,
            reason=mapping.reason,
            mapping=mapping,
            safety=safety,
        )

    return EvidenceEligibilityResult(
        eligible=True,
        error_code=None,
        reason=None,
        mapping=mapping,
        safety=safety,
    )
