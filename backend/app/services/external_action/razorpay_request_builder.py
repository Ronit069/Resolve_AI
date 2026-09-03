"""
Module J, Category D — Razorpay request body construction.

Pure, local, no HTTP. Builds the request bodies for the two Razorpay
endpoints Category D calls:
  - POST /v1/documents               (a single evidence document upload)
  - PATCH /v1/disputes/:id/contest   (contest draft/submit)

Never emits any evidence field outside H-08's frozen
EVIDENCE_TYPE_TO_RAZORPAY_FIELD mapping (shipping_proof,
customer_communication, proof_of_service, term_and_conditions,
refund_confirmation) — Razorpay's other documented contest evidence
fields (e.g. explanation_letter, proof_of_right, access_activity_log,
refund_cancellation_policy, others) are out of scope for the ResolveAI
MVP and are never populated here.
"""

import dataclasses
from typing import Dict, List
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.module_a import Dispute
from app.models.module_h import ContestPackage, ContestPackageDocument, RazorpayDocumentLink


class RazorpayDocumentLinkMissing(Exception):
    """
    Raised when an approved ContestPackageDocument has no corresponding
    RazorpayDocumentLink yet — i.e. its document has not been uploaded to
    Razorpay (and its Razorpay document ID recorded) before a contest
    draft/submit request is attempted. Purely local; never a network
    error. Treated as a terminal, never-retried dispatch failure —
    retrying without first uploading the missing document would fail
    identically every time.
    """
    def __init__(self, document_id: UUID):
        self.document_id = document_id
        super().__init__(f"No RazorpayDocumentLink found for document_id={document_id}; upload must complete first")


@dataclasses.dataclass(frozen=True)
class ContestRequestBuild:
    razorpay_dispute_id: str
    body: dict


def build_upload_document_request() -> dict:
    """
    Non-file form fields for POST /v1/documents. The file bytes/name/
    content-type are attached separately by the HTTP client (multipart
    field name "file", per the frozen Category D contract) — this
    function only returns the purpose field.
    """
    return {"purpose": "dispute_evidence"}


def build_contest_request(
    db: Session,
    case_id: UUID,
    package: ContestPackage,
    action: str,
) -> ContestRequestBuild:
    """
    Builds the PATCH /v1/disputes/:id/contest request body for the given
    ContestPackage and action ("draft" | "submit").

    Groups the package's approved documents by their frozen H-08
    razorpay_evidence_field, resolving each to its Razorpay document ID
    via RazorpayDocumentLink (never the internal document_id). Raises
    RazorpayDocumentLinkMissing — a local, non-network error — if any
    approved document has not yet been uploaded/linked.
    """
    dispute = (
        db.query(Dispute)
        .filter(Dispute.case_id == case_id)
        .populate_existing()
        .first()
    )
    if dispute is None:
        raise ValueError(f"No dispute found for case_id={case_id}")

    package_documents = (
        db.query(ContestPackageDocument)
        .filter(ContestPackageDocument.contest_package_id == package.id, ContestPackageDocument.approved == True)
        .order_by(ContestPackageDocument.sort_order)
        .all()
    )

    evidence: Dict[str, List[str]] = {}
    for pkg_doc in package_documents:
        link = (
            db.query(RazorpayDocumentLink)
            .filter(RazorpayDocumentLink.document_id == pkg_doc.document_id)
            .populate_existing()
            .first()
        )
        if link is None:
            raise RazorpayDocumentLinkMissing(pkg_doc.document_id)
        evidence.setdefault(pkg_doc.razorpay_evidence_field, []).append(link.razorpay_document_id)

    body = {
        "action": action,
        "amount": package.contest_amount_minor,
        "summary": package.summary,
        "evidence": evidence,
    }

    return ContestRequestBuild(razorpay_dispute_id=dispute.external_dispute_id, body=body)
