"""
Module J, Category B — outbox writer.

Composes the existing, frozen H-06/H-07/H-08/H-10/H-12 gates (and, for
consistency confirmation only, H-05) with a fresh canonical re-read
immediately before creating any ExternalActionOutbox row. No gate's
internal logic is duplicated or modified here — each is called exactly
as it already exists.

No external HTTP call is made anywhere in this module. `payload_json` on
each written outbox row contains only locally-known, already-validated
fields (amount, summary, document/field pairs) — it is not a Razorpay
request body and invents no Razorpay wire format.
"""

import dataclasses
import hashlib
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.module_a import Dispute
from app.models.module_f import RiskPrediction
from app.models.module_h import (
    ContestPackage,
    ContestPackageDocument,
    ContestPackageStatus,
    ExternalActionOutbox,
    ExternalActionType,
    OutboxStatus,
    ReviewQueueItem,
    QueueStatus,
    ReviewActionEnum,
)
from app.services.external_action.deadline_gate import check_dispute_actionable
from app.services.external_action.contest_amount_gate import validate_contest_amount
from app.services.external_action.contest_summary_gate import validate_contest_summary
from app.services.external_action.evidence_mapping_gate import evaluate_evidence_for_contest, EvidenceType
from app.services.external_action.contest_submission_action_gate import determine_contest_submission_action
from app.services.review.dual_control import requires_dual_approval

from app.models.module_c import EvidenceDocument


@dataclasses.dataclass(frozen=True)
class GateCheckSummary:
    h06_allowed: bool
    h06_reason: Optional[str]
    h07_allowed: bool
    h07_reason: Optional[str]
    h08_all_documents_eligible: bool
    h08_reason: Optional[str]
    h10_action: Optional[str]  # "submit" | "draft"
    h12_allowed: bool
    h12_reason: Optional[str]
    h05_dual_control_required: Optional[bool]  # informational only — not re-decided, see module docstring


@dataclasses.dataclass(frozen=True)
class OutboxWriteResult:
    written: bool
    reason: Optional[str]
    gate_summary: Optional[GateCheckSummary]
    created_outbox_ids: List[UUID]
    skipped_existing_outbox_ids: List[UUID]
    checked_at: datetime


def _idempotency_key_upload(contest_package_id: UUID, document_id: UUID) -> str:
    raw = f"contest_package:{contest_package_id}:upload:{document_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _idempotency_key_contest(contest_package_id: UUID, action: str) -> str:
    raw = f"contest_package:{contest_package_id}:contest:{action}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_or_create_outbox_row(
    db: Session,
    case_id: UUID,
    action_type: ExternalActionType,
    aggregate_id: UUID,
    payload_json: dict,
    idempotency_key: str,
) -> tuple[UUID, bool]:
    """
    Returns (outbox_id, created). If a row with this idempotency_key
    already exists, it is returned unchanged (created=False) rather than
    duplicated — the existing ExternalActionOutbox.idempotency_key UNIQUE
    constraint is the source of truth, not new application logic.
    """
    existing = (
        db.query(ExternalActionOutbox)
        .filter(ExternalActionOutbox.idempotency_key == idempotency_key)
        .populate_existing()
        .first()
    )
    if existing is not None:
        return existing.id, False

    row = ExternalActionOutbox(
        case_id=case_id,
        action_type=action_type,
        aggregate_id=aggregate_id,
        payload_json=payload_json,
        idempotency_key=idempotency_key,
        status=OutboxStatus.PENDING,
        attempt_count=0,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # Concurrent writer won the race on the same idempotency_key.
        db.rollback()
        existing = (
            db.query(ExternalActionOutbox)
            .filter(ExternalActionOutbox.idempotency_key == idempotency_key)
            .populate_existing()
            .first()
        )
        return existing.id, False
    return row.id, True


def write_outbox_for_package(
    db: Session,
    case_id: UUID,
    contest_package_id: UUID,
    current_time: Optional[datetime] = None,
) -> OutboxWriteResult:
    """
    Fresh-re-validates a previously-assembled ContestPackage against
    H-06/H-07/H-08/H-10/H-12 and, only if every check passes, writes:
      - one UPLOAD_DOCUMENT outbox row per approved ContestPackageDocument
      - one CONTEST_DRAFT or CONTEST_SUBMIT outbox row, per H-10's fresh
        action determination

    Never writes an ACCEPT outbox row (H-17 Clause B is out of scope).
    If any gate fails, no outbox row is written and ContestPackage.status
    is left unchanged (stays DRAFT) — no external action is ever implied.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    package = (
        db.query(ContestPackage)
        .filter(ContestPackage.id == contest_package_id, ContestPackage.case_id == case_id)
        .populate_existing()
        .first()
    )
    if package is None:
        return OutboxWriteResult(
            written=False, reason="ContestPackage not found for this case", gate_summary=None,
            created_outbox_ids=[], skipped_existing_outbox_ids=[], checked_at=current_time,
        )

    # H-06: fresh deadline/status recheck.
    h06 = check_dispute_actionable(db, case_id, current_time=current_time)

    # H-07: fresh amount recheck against the package's own recorded amount
    # (itself already fresh-derived from the current draft at assembly
    # time) — never trusts a caller-supplied amount.
    h07 = validate_contest_amount(db, case_id, package.contest_amount_minor, current_time=current_time)

    # H-12: fresh summary/guardrail/claim recheck against the package's
    # own recorded summary.
    h12 = validate_contest_summary(db, case_id, package.summary, current_time=current_time)

    # H-08: fresh per-document recheck for every document already
    # approved into this package at assembly time. Fail-closed: if any
    # previously-eligible document is no longer safe/mapped, the whole
    # write is blocked rather than silently dropping that document.
    package_documents = (
        db.query(ContestPackageDocument)
        .filter(ContestPackageDocument.contest_package_id == package.id, ContestPackageDocument.approved == True)
        .order_by(ContestPackageDocument.sort_order)
        .all()
    )
    h08_all_eligible = True
    h08_reason = None
    for pkg_doc in package_documents:
        doc = db.query(EvidenceDocument).filter(EvidenceDocument.document_id == pkg_doc.document_id).populate_existing().first()
        if doc is None:
            h08_all_eligible = False
            h08_reason = f"Document {pkg_doc.document_id} no longer exists"
            break
        try:
            evidence_type = EvidenceType(doc.evidence_type)
        except ValueError:
            h08_all_eligible = False
            h08_reason = f"Document {pkg_doc.document_id} has an unrecognized evidence_type"
            break
        eligibility = evaluate_evidence_for_contest(db, case_id, doc.document_id, evidence_type, current_time=current_time)
        if not eligibility.eligible:
            h08_all_eligible = False
            h08_reason = eligibility.reason
            break

    # H-10: fresh draft-vs-submit determination.
    h10 = determine_contest_submission_action(db, case_id, current_time=current_time)

    # H-05: composed for consistency confirmation only — NOT re-decided.
    # The DONE queue state H-10 already requires could only exist today
    # because review.py's dual-control gate at approval time already
    # enforced this; calling requires_dual_approval here re-derives its
    # informational value (was dual control required for this case?) for
    # the audit trail without gating on it a second time.
    dispute = db.query(Dispute).filter(Dispute.case_id == case_id).populate_existing().first()
    prediction = (
        db.query(RiskPrediction)
        .filter(RiskPrediction.case_id == case_id)
        .order_by(RiskPrediction.created_at.desc())
        .populate_existing()
        .first()
    )
    h05_required = None
    if dispute is not None and prediction is not None:
        h05_required = requires_dual_approval(ReviewActionEnum.APPROVE_CONTEST, prediction, dispute)

    gate_summary = GateCheckSummary(
        h06_allowed=h06.allowed, h06_reason=h06.reason,
        h07_allowed=h07.allowed, h07_reason=h07.reason,
        h08_all_documents_eligible=h08_all_eligible, h08_reason=h08_reason,
        h10_action=h10.action,
        h12_allowed=h12.allowed, h12_reason=h12.reason,
        h05_dual_control_required=h05_required,
    )

    all_gates_pass = h06.allowed and h07.allowed and h12.allowed and h08_all_eligible
    if not all_gates_pass:
        reasons = [r for r in (h06.reason, h07.reason, h12.reason, h08_reason) if r]
        return OutboxWriteResult(
            written=False, reason="; ".join(reasons) or "Gate validation failed",
            gate_summary=gate_summary, created_outbox_ids=[], skipped_existing_outbox_ids=[],
            checked_at=current_time,
        )

    created_ids: List[UUID] = []
    skipped_ids: List[UUID] = []

    for pkg_doc in package_documents:
        idem_key = _idempotency_key_upload(package.id, pkg_doc.document_id)
        outbox_id, created = _get_or_create_outbox_row(
            db, case_id, ExternalActionType.UPLOAD_DOCUMENT, pkg_doc.document_id,
            {"document_id": str(pkg_doc.document_id), "razorpay_evidence_field": pkg_doc.razorpay_evidence_field},
            idem_key,
        )
        (created_ids if created else skipped_ids).append(outbox_id)

    contest_action_type = (
        ExternalActionType.CONTEST_SUBMIT if h10.action == "submit" else ExternalActionType.CONTEST_DRAFT
    )
    contest_idem_key = _idempotency_key_contest(package.id, h10.action)
    contest_outbox_id, contest_created = _get_or_create_outbox_row(
        db, case_id, contest_action_type, package.id,
        {
            "contest_amount_minor": package.contest_amount_minor,
            "summary": package.summary,
            "action": h10.action,
            "document_ids": [str(d.document_id) for d in package_documents],
        },
        contest_idem_key,
    )
    (created_ids if contest_created else skipped_ids).append(contest_outbox_id)

    package.status = ContestPackageStatus.APPROVED
    db.commit()

    return OutboxWriteResult(
        written=True, reason=None, gate_summary=gate_summary,
        created_outbox_ids=created_ids, skipped_existing_outbox_ids=skipped_ids, checked_at=current_time,
    )
