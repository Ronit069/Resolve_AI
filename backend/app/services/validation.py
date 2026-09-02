import hashlib
import json
import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Optional

from app.models.shared import Case, AuditLog
from app.models.module_a import Dispute
from app.models.module_b import Payment
from app.models.module_c import EvidenceDocument
from app.models.module_d import DocumentExtraction
from app.models.module_e import EvidencePolicyVersion, EvidenceValidationRun, EValidationRunStatus

class PolicyResolutionError(Exception):
    pass

class PolicyInputUnavailable(PolicyResolutionError):
    pass

class PolicyNotFound(PolicyResolutionError):
    pass

class PolicyAmbiguous(PolicyResolutionError):
    pass

def resolve_policy(
    db: Session,
    payment_network: str,
    reason_code: str,
    phase: str,
    timestamp: datetime
) -> EvidencePolicyVersion:
    policies = db.query(EvidencePolicyVersion).filter(
        EvidencePolicyVersion.payment_network == payment_network,
        EvidencePolicyVersion.reason_code == reason_code,
        EvidencePolicyVersion.phase == phase,
        EvidencePolicyVersion.effective_from <= timestamp
    ).all()
    
    valid_policies = []
    for p in policies:
        if p.effective_to is None:
            valid_policies.append(p)
        else:
            p_eff_to = p.effective_to
            if p_eff_to.tzinfo is None:
                p_eff_to = p_eff_to.replace(tzinfo=timezone.utc)
            if timestamp < p_eff_to:
                valid_policies.append(p)
            
    if len(valid_policies) == 0:
        raise PolicyNotFound("No matching policy version found.")
    elif len(valid_policies) > 1:
        raise PolicyAmbiguous("Multiple overlapping policy versions found.")
        
    return valid_policies[0]

def compute_evidence_version(db: Session, case_id: uuid.UUID) -> str:
    documents = db.query(EvidenceDocument).filter(
        EvidenceDocument.case_id == case_id
    ).order_by(EvidenceDocument.document_id).all()
    
    doc_state_list = []
    
    for doc in documents:
        extraction = db.query(DocumentExtraction).filter(
            DocumentExtraction.document_id == doc.document_id
        ).first()
        
        doc_dict = {
            "document_id": str(doc.document_id),
            "sha256": doc.sha256,
            "evidence_type": doc.evidence_type,
            "processing_status": doc.processing_status,
            "extraction_schema_version": None,
            "extraction_status": None,
        }
        
        if extraction:
            doc_dict["extraction_schema_version"] = extraction.schema_version
            doc_dict["extraction_status"] = extraction.extraction_status
            
        doc_state_list.append(doc_dict)
        
    serialized = json.dumps(doc_state_list, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()

def prepare_validation_run(db: Session, case_id: str | uuid.UUID, merchant_id: str | uuid.UUID) -> tuple[EvidenceValidationRun, bool]:
    # Ensure they are UUID objects for SQLAlchemy
    case_id_obj = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(case_id)
    merchant_id_obj = merchant_id if isinstance(merchant_id, uuid.UUID) else uuid.UUID(merchant_id)
    
    # 1. Fetch Case and verify tenant access
    case = db.query(Case).filter(
        Case.case_id == case_id_obj,
        Case.merchant_id == merchant_id_obj
    ).first()
    if not case:
        raise ValueError("Case not found or access denied")
        
    dispute = db.query(Dispute).filter(Dispute.case_id == case.case_id).first()
    if not dispute:
        raise ValueError("Dispute not found")
        
    payment = db.query(Payment).filter(Payment.case_id == case.case_id).first()
    if not payment:
        raise PolicyInputUnavailable("Payment not found")
        
    if not payment.network:
        raise PolicyInputUnavailable("Payment network is unavailable")
        
    policy = resolve_policy(
        db,
        payment_network=payment.network,
        reason_code=dispute.reason_code,
        phase=dispute.phase,
        timestamp=dispute.dispute_created_at
    )
    
    evidence_version = compute_evidence_version(db, case.case_id)
    
    # Idempotency key
    key_dict = {
        "case_id": str(case.case_id),
        "evidence_version": evidence_version,
        "policy_version_id": str(policy.policy_version_id)
    }
    key_serialized = json.dumps(key_dict, sort_keys=True, separators=(',', ':'))
    idempotency_key = hashlib.sha256(key_serialized.encode('utf-8')).hexdigest()
    
    # Check if run exists
    existing_run = db.query(EvidenceValidationRun).filter(
        EvidenceValidationRun.idempotency_key == idempotency_key
    ).first()
    
    if existing_run:
        return existing_run, True
        
    new_run = EvidenceValidationRun(
        case_id=case.case_id,
        evidence_version=evidence_version,
        policy_version_id=policy.policy_version_id,
        status=EValidationRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        idempotency_key=idempotency_key
    )
    
    db.add(new_run)
    try:
        db.flush()
        
        # Create Audit Log for new run
        audit = AuditLog(
            case_id=case.case_id,
            action="EVIDENCE_VALIDATION_REQUESTED",
            details=json.dumps({
                "validation_run_id": str(new_run.id),
                "case_id": str(case.case_id),
                "evidence_version": evidence_version,
                "policy_version_id": str(policy.policy_version_id)
            })
        )
        db.add(audit)
        db.commit()
        return new_run, False
        
    except IntegrityError:
        db.rollback()
        existing_run = db.query(EvidenceValidationRun).filter(
            EvidenceValidationRun.idempotency_key == idempotency_key
        ).first()
        if existing_run:
            return existing_run, True
        raise
