"""
Module J, Category B — ContestPackage / ContestPackageDocument assembly.

Pure local read/compose/write: no external HTTP call, no Razorpay contract
knowledge required. Builds the locally-authoritative record of what an
approved APPROVE_CONTEST decision actually contains, using only already-
frozen H3/H4 schema (H-00) and the already-frozen H-08 evidence gate.

This module does NOT decide whether the package is safe to dispatch —
that fresh re-validation (H-06/H-07/H-08/H-10/H-12) happens in
outbox_writer.py, immediately before any ExternalActionOutbox row is
written, per the "fresh read, not stale snapshot" discipline every H gate
in this codebase already follows.
"""

import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.shared import Case
from app.models.module_c import EvidenceDocument, EvidenceType
from app.models.module_g import GeneratedDraft
from app.models.module_h import (
    ReviewAction,
    ReviewActionEnum,
    ContestPackage,
    ContestPackageDocument,
    ContestPackageStatus,
)
from app.services.external_action.evidence_mapping_gate import evaluate_evidence_for_contest


@dataclasses.dataclass(frozen=True)
class AssembledDocument:
    document_id: UUID
    razorpay_evidence_field: str


@dataclasses.dataclass(frozen=True)
class ContestPackageAssemblyResult:
    assembled: bool
    reason: Optional[str]
    contest_package_id: Optional[UUID]
    package_hash: Optional[str]
    documents: List[AssembledDocument]
    checked_at: datetime


def _compute_package_hash(
    case_id: UUID,
    review_action_id: UUID,
    draft_id: UUID,
    contest_amount_minor: int,
    summary: str,
    documents: List[AssembledDocument],
) -> str:
    """
    Deterministic for identical logical package contents: same inputs
    (including the same set of eligible documents, order-independent)
    always produce the same hash. Uses only locally-known fields — no
    Razorpay response data is or could be included at assembly time.
    """
    canonical = {
        "case_id": str(case_id),
        "review_action_id": str(review_action_id),
        "draft_id": str(draft_id),
        "contest_amount_minor": int(contest_amount_minor),
        "summary": summary,
        "documents": sorted(
            [{"document_id": str(d.document_id), "razorpay_evidence_field": d.razorpay_evidence_field} for d in documents],
            key=lambda d: d["document_id"],
        ),
    }
    canonical_json = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def assemble_contest_package(
    db: Session,
    case_id: UUID,
    review_action_id: UUID,
    current_time: Optional[datetime] = None,
) -> ContestPackageAssemblyResult:
    """
    Assemble (or return the existing) ContestPackage for one finalized
    APPROVE_CONTEST ReviewAction. Fresh-reads the canonical ReviewAction,
    current GeneratedDraft, and every EvidenceDocument for the case —
    never accepts caller-supplied amounts, summaries, or document lists.

    Only evidence documents that pass H-08's fresh
    evaluate_evidence_for_contest check become ContestPackageDocument
    rows (approved=True); no additional evidence vocabulary is invented
    beyond H-08's existing EVIDENCE_TYPE_TO_RAZORPAY_FIELD mapping.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    case = db.query(Case).filter(Case.case_id == case_id).populate_existing().first()
    if case is None:
        return ContestPackageAssemblyResult(
            assembled=False, reason="Case not found", contest_package_id=None,
            package_hash=None, documents=[], checked_at=current_time,
        )

    review_action = (
        db.query(ReviewAction)
        .filter(ReviewAction.id == review_action_id, ReviewAction.case_id == case_id)
        .populate_existing()
        .first()
    )
    if review_action is None:
        return ContestPackageAssemblyResult(
            assembled=False, reason="ReviewAction not found for this case", contest_package_id=None,
            package_hash=None, documents=[], checked_at=current_time,
        )
    if review_action.action != ReviewActionEnum.APPROVE_CONTEST:
        return ContestPackageAssemblyResult(
            assembled=False, reason="ReviewAction is not APPROVE_CONTEST", contest_package_id=None,
            package_hash=None, documents=[], checked_at=current_time,
        )

    # Idempotent re-entry: an already-assembled package for this exact
    # review action is returned as-is rather than duplicated. The schema
    # has no UNIQUE constraint on ContestPackage.review_action_id (a known,
    # documented pre-existing gap — not repaired here), so this check is
    # done at the application level.
    existing = (
        db.query(ContestPackage)
        .filter(ContestPackage.review_action_id == review_action_id)
        .populate_existing()
        .first()
    )
    if existing is not None:
        existing_docs = (
            db.query(ContestPackageDocument)
            .filter(ContestPackageDocument.contest_package_id == existing.id)
            .order_by(ContestPackageDocument.sort_order)
            .all()
        )
        return ContestPackageAssemblyResult(
            assembled=True, reason="Already assembled", contest_package_id=existing.id,
            package_hash=existing.package_hash,
            documents=[AssembledDocument(d.document_id, d.razorpay_evidence_field) for d in existing_docs],
            checked_at=current_time,
        )

    draft = (
        db.query(GeneratedDraft)
        .filter(GeneratedDraft.case_id == case_id, GeneratedDraft.is_current == True)
        .populate_existing()
        .first()
    )
    if draft is None:
        return ContestPackageAssemblyResult(
            assembled=False, reason="No current GeneratedDraft for this case", contest_package_id=None,
            package_hash=None, documents=[], checked_at=current_time,
        )
    if draft.contest_amount_minor is None:
        return ContestPackageAssemblyResult(
            assembled=False, reason="GeneratedDraft has no contest_amount_minor", contest_package_id=None,
            package_hash=None, documents=[], checked_at=current_time,
        )

    evidence_documents = (
        db.query(EvidenceDocument)
        .filter(EvidenceDocument.case_id == case_id)
        .populate_existing()
        .all()
    )

    assembled_docs: List[AssembledDocument] = []
    for doc in evidence_documents:
        try:
            evidence_type = EvidenceType(doc.evidence_type)
        except ValueError:
            continue
        eligibility = evaluate_evidence_for_contest(
            db, case_id, doc.document_id, evidence_type, current_time=current_time
        )
        if eligibility.eligible:
            assembled_docs.append(
                AssembledDocument(
                    document_id=doc.document_id,
                    razorpay_evidence_field=eligibility.mapping.razorpay_evidence_field,
                )
            )

    contest_amount_minor = int(draft.contest_amount_minor)
    package_hash = _compute_package_hash(
        case_id, review_action_id, draft.id, contest_amount_minor, draft.summary, assembled_docs
    )

    package = ContestPackage(
        case_id=case_id,
        review_action_id=review_action_id,
        draft_id=draft.id,
        contest_amount_minor=contest_amount_minor,
        summary=draft.summary,
        package_hash=package_hash,
        status=ContestPackageStatus.DRAFT,
    )
    db.add(package)
    db.flush()

    for sort_order, assembled_doc in enumerate(assembled_docs):
        db.add(
            ContestPackageDocument(
                contest_package_id=package.id,
                document_id=assembled_doc.document_id,
                razorpay_evidence_field=assembled_doc.razorpay_evidence_field,
                approved=True,
                sort_order=sort_order,
            )
        )

    db.commit()
    db.refresh(package)

    return ContestPackageAssemblyResult(
        assembled=True, reason=None, contest_package_id=package.id,
        package_hash=package.package_hash, documents=assembled_docs, checked_at=current_time,
    )
