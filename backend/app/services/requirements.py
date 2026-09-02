import uuid
from typing import List
from sqlalchemy.orm import Session
from app.models.module_c import EvidenceRequirement, CaseEvidenceStatus, EvidenceDocument, EvidenceProcessingStatus, RequirementLevel, EvidenceType
from app.models.module_a import Dispute
from app.models.shared import Case, ProcessingState, AuditLog

def setup_default_requirements(db: Session):
    """
    Deterministically seeds default evidence requirements if they do not exist.
    """
    if db.query(EvidenceRequirement).first():
        return # Already seeded
        
    defaults = [
        EvidenceRequirement(reason_code="fraudulent", evidence_type=EvidenceType.PROOF_OF_DELIVERY, requirement_level=RequirementLevel.REQUIRED),
        EvidenceRequirement(reason_code="fraudulent", evidence_type=EvidenceType.INVOICE, requirement_level=RequirementLevel.REQUIRED),
        EvidenceRequirement(reason_code="fraudulent", evidence_type=EvidenceType.CUSTOMER_COMMUNICATION, requirement_level=RequirementLevel.OPTIONAL),
        
        EvidenceRequirement(reason_code="product_not_received", evidence_type=EvidenceType.PROOF_OF_DELIVERY, requirement_level=RequirementLevel.REQUIRED),
        EvidenceRequirement(reason_code="product_not_received", evidence_type=EvidenceType.COURIER_TRACKING, requirement_level=RequirementLevel.REQUIRED),
        
        EvidenceRequirement(reason_code="subscription_canceled", evidence_type=EvidenceType.TERMS_ACCEPTANCE, requirement_level=RequirementLevel.REQUIRED),
        EvidenceRequirement(reason_code="subscription_canceled", evidence_type=EvidenceType.CUSTOMER_COMMUNICATION, requirement_level=RequirementLevel.RECOMMENDED),
    ]
    
    for req in defaults:
        db.add(req)
    db.commit()

def evaluate_case_evidence_coverage(db: Session, case_id: uuid.UUID) -> CaseEvidenceStatus:
    case = db.query(Case).filter(Case.case_id == case_id).first()
    dispute = db.query(Dispute).filter(Dispute.case_id == case_id).first()
    
    if not case or not dispute:
        return None
        
    reason_code = dispute.reason_code
    
    # 1. Fetch requirements for this reason code
    requirements = db.query(EvidenceRequirement).filter(
        EvidenceRequirement.reason_code == reason_code,
        EvidenceRequirement.active == True
    ).all()
    
    required_reqs = [r for r in requirements if r.requirement_level == RequirementLevel.REQUIRED]
    
    # 2. Fetch all READY_FOR_OCR evidence for this case
    available_evidence = db.query(EvidenceDocument).filter(
        EvidenceDocument.case_id == case_id,
        EvidenceDocument.processing_status == EvidenceProcessingStatus.READY_FOR_OCR
    ).all()
    
    available_types = set([doc.evidence_type for doc in available_evidence])
    
    # 3. Calculate coverage
    required_count = len(required_reqs)
    available_required_count = 0
    missing_required = []
    
    for req in required_reqs:
        if req.evidence_type in available_types:
            available_required_count += 1
        else:
            missing_required.append(req.evidence_type)
            
    coverage_ratio = 0.0
    if required_count > 0:
        coverage_ratio = float(available_required_count) / float(required_count)
    else:
        # Edge case: If there are 0 requirements, coverage is considered 100% (1.0)
        coverage_ratio = 1.0
        
    # 4. Upsert CaseEvidenceStatus
    status_record = db.query(CaseEvidenceStatus).filter(CaseEvidenceStatus.case_id == case_id).first()
    if not status_record:
        status_record = CaseEvidenceStatus(
            case_id=case_id,
            reason_code=reason_code
        )
        db.add(status_record)
        
    status_record.required_count = required_count
    status_record.available_required_count = available_required_count
    status_record.missing_required = missing_required
    status_record.coverage_ratio = coverage_ratio
    
    # 5. Check Case Transition
    # Only transition if we are in AWAITING_EVIDENCE or ENRICHED and coverage is 1.0 (or no requirements)
    if coverage_ratio == 1.0 and case.processing_state in [ProcessingState.AWAITING_EVIDENCE, ProcessingState.ENRICHED]:
        case.processing_state = ProcessingState.EVIDENCE_READY
        audit = AuditLog(
            case_id=case_id,
            action="EVIDENCE_READY",
            details="Required evidence coverage reached 100%."
        )
        db.add(audit)
        
    db.commit()
    db.refresh(status_record)
    return status_record
