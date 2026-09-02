import os
import uuid
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import UploadFile, HTTPException
from app.models.shared import Case, ProcessingState, AuditLog, ProcessingError, AppUser
from app.models.module_c import EvidenceDocument, EvidenceProcessingStatus, EvidenceType, CaseEvidenceStatus, EvidenceAccessEvent, EvidenceRequirement, RequirementLevel
from app.services.security import validate_and_hash_upload, generate_secure_object_key
from app.core.storage import storage_client
from app.worker.tasks import scan_evidence_task

def upload_evidence_to_case(db: Session, case_id: uuid.UUID, user_id: uuid.UUID, upload_file: UploadFile, evidence_type: EvidenceType) -> EvidenceDocument:
    # 1. Validate case and tenant access
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
        
    user = db.query(AppUser).filter(AppUser.user_id == user_id).first()
    if not user or case.merchant_id != user.merchant_id:
        raise HTTPException(status_code=403, detail="Unauthorized access to this case.")
        
    # 2. File validation & SHA256 (Streaming)
    validation_result = validate_and_hash_upload(upload_file)

    # 3. Duplicate detection
    existing = db.query(EvidenceDocument).filter(
        EvidenceDocument.case_id == case_id,
        EvidenceDocument.sha256 == validation_result.sha256_hash
    ).first()
    if existing:
        # Avoid uploading again, just return the existing record or throw a 409 depending on preference
        raise HTTPException(status_code=409, detail="Duplicate evidence detected for this case.")
        
    # 4. Generate object key
    object_key = generate_secure_object_key(str(case_id))
    
    # 5. Store file in MinIO
    if not storage_client.upload_file(upload_file.file, object_key, content_type=validation_result.mime_type):
        raise HTTPException(status_code=500, detail="Failed to upload file to storage.")
        
    # 6. Persist database metadata with compensation
    try:
        new_evidence = EvidenceDocument(
            case_id=case_id,
            merchant_id=case.merchant_id,
            evidence_type=evidence_type,
            object_key=object_key,
            original_filename=upload_file.filename,
            mime_type=validation_result.mime_type,
            file_size_bytes=validation_result.file_size,
            sha256=validation_result.sha256_hash,
            processing_status=EvidenceProcessingStatus.QUARANTINED
        )
        db.add(new_evidence)
        db.flush() # flush to get document_id without full commit
        
        # Log audit events
        audit = AuditLog(
            case_id=case_id,
            user_id=user_id,
            action="EVIDENCE_UPLOADED",
            details=f"Uploaded {evidence_type.value} file, doc_id: {new_evidence.document_id}"
        )
        db.add(audit)
        
        access_event = EvidenceAccessEvent(
            document_id=new_evidence.document_id,
            case_id=case_id,
            user_id=user_id,
            action="UPLOAD",
            result="SUCCESS"
        )
        db.add(access_event)
        
        # Advance Case State if it's ENRICHED
        if case.processing_state == ProcessingState.ENRICHED:
            case.processing_state = ProcessingState.AWAITING_EVIDENCE
            
        db.commit()
        db.refresh(new_evidence)
    except Exception as e:
        db.rollback()
        # Compensation: delete the uploaded file to prevent orphan objects
        storage_client.delete_file(object_key)
        raise HTTPException(status_code=500, detail=f"Database persistence failed: {str(e)}")
        
    # 7. Asynchronously trigger malware scanning
    scan_evidence_task.delay(str(new_evidence.document_id))
    
    return new_evidence

def list_case_evidence(db: Session, case_id: uuid.UUID, user_id: uuid.UUID) -> List[EvidenceDocument]:
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
        
    user = db.query(AppUser).filter(AppUser.user_id == user_id).first()
    if not user or case.merchant_id != user.merchant_id:
        raise HTTPException(status_code=403, detail="Unauthorized access to this case.")
        
    return db.query(EvidenceDocument).filter(EvidenceDocument.case_id == case_id).all()
