import uuid
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user, get_current_merchant
from app.schemas.module_c import CaseEvidenceResponse, EvidenceDocumentResponse, EvidenceSummarySchema, EvidenceType
from app.services.evidence import upload_evidence_to_case, list_case_evidence
from app.services.requirements import evaluate_case_evidence_coverage, setup_default_requirements
from app.models.shared import Case, Merchant, AppUser

router = APIRouter()

@router.post("/{case_id}/evidence", response_model=EvidenceDocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_evidence(
    case_id: uuid.UUID,
    evidence_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
    current_merchant: Merchant = Depends(get_current_merchant)
):
    """
    Upload evidence for a specific case.
    Validates file content, sizes, prevents path traversal, and triggers malware scan.
    """
    try:
        ev_type_enum = EvidenceType(evidence_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid evidence type.")

    setup_default_requirements(db) # Ensure defaults exist for MVP

    # Tenant isolation: the case must belong to the authenticated caller's
    # own (server-derived) merchant, checked BEFORE any file is validated,
    # stored, or persisted. Cross-tenant access is deliberately
    # indistinguishable from not-found, matching the existing
    # anti-enumeration convention used by document-intelligence / audit.py.
    case = db.query(Case).filter(
        Case.case_id == case_id,
        Case.merchant_id == current_merchant.merchant_id,
    ).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")

    return upload_evidence_to_case(db, case_id, current_user.user_id, file, ev_type_enum)

@router.get("/{case_id}/evidence", response_model=CaseEvidenceResponse)
def get_case_evidence(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
    current_merchant: Merchant = Depends(get_current_merchant)
):
    """
    List all evidence documents for a case and return the coverage summary.
    """
    setup_default_requirements(db) # Ensure defaults exist for MVP

    # Tenant isolation: same server-derived merchant check as upload above.
    case = db.query(Case).filter(
        Case.case_id == case_id,
        Case.merchant_id == current_merchant.merchant_id,
    ).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")

    documents = list_case_evidence(db, case_id, current_user.user_id)
    
    # Refresh coverage just in case
    coverage = evaluate_case_evidence_coverage(db, case_id)
    
    # If no requirements exist yet or coverage not initialized
    if not coverage:
        summary = EvidenceSummarySchema(
            required_count=0,
            available_required_count=0,
            missing_required=[],
            coverage_ratio=1.0
        )
    else:
        summary = EvidenceSummarySchema(
            required_count=coverage.required_count,
            available_required_count=coverage.available_required_count,
            missing_required=[EvidenceType(t) for t in coverage.missing_required],
            coverage_ratio=float(coverage.coverage_ratio)
        )
        
    return CaseEvidenceResponse(
        case_id=case_id,
        evidence=documents,
        evidence_summary=summary,
        processing_state=case.processing_state.value
    )

@router.get("/{case_id}/document-intelligence")
def get_case_document_intelligence(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant)
):
    from app.models.module_d import CaseDocumentIntelligenceStatus

    # Tenant isolation: the case must belong to the authenticated caller's
    # own (server-derived) merchant. Cross-tenant access is deliberately
    # indistinguishable from not-found, matching the existing
    # anti-enumeration convention used by audit.py / review.py.
    case = db.query(Case).filter(
        Case.case_id == case_id,
        Case.merchant_id == current_merchant.merchant_id,
    ).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
        
    status_row = db.query(CaseDocumentIntelligenceStatus).filter_by(case_id=case_id).first()
    
    if not status_row:
        return {
            "overall_status": "PENDING",
            "total_safe_documents": 0,
            "processed_documents": 0,
            "review_required_documents": 0,
            "failed_documents": 0,
            "ready_for_module_e": False
        }
        
    return {
        "overall_status": status_row.overall_status,
        "total_safe_documents": status_row.total_safe_documents,
        "processed_documents": status_row.processed_documents,
        "review_required_documents": status_row.review_required_documents,
        "failed_documents": status_row.failed_documents,
        "ready_for_module_e": status_row.ready_for_module_e
    }
