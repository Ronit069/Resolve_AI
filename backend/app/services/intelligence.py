from uuid import UUID
from datetime import datetime, timezone
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import hashlib

from app.models.module_c import EvidenceDocument, EvidenceProcessingStatus, ScanStatus
from app.models.module_d import DocumentProcessingJob
from app.models.shared import Case, Merchant, AuditLog

class IntelligenceError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(self.message)

def generate_idempotency_key(document_id: UUID, pipeline_version: str) -> str:
    # Use sha256 to create a deterministic bounded key
    raw_key = f"{document_id}::{pipeline_version}".encode('utf-8')
    return hashlib.sha256(raw_key).hexdigest()

def request_document_processing(
    db: Session,
    document_id: UUID,
    merchant_id: UUID,
    user_id: Optional[UUID],
    pipeline_version: str = "v1.0"
) -> Tuple[DocumentProcessingJob, bool]:
    """
    Validates eligibility and creates or reuses an idempotent processing job.
    Returns (job, created). If created=True, caller should dispatch Celery task.
    """
    # 1. Look up the document scoped to the merchant
    document = db.query(EvidenceDocument).filter(
        EvidenceDocument.document_id == document_id,
        EvidenceDocument.merchant_id == merchant_id
    ).first()

    if not document:
        raise IntelligenceError("OBJECT_NOT_FOUND", "Document not found or access denied")

    # 2. Look up case to ensure it exists and matches merchant
    case = db.query(Case).filter(
        Case.case_id == document.case_id,
        Case.merchant_id == merchant_id
    ).first()

    if not case:
        raise IntelligenceError("TENANT_MISMATCH", "Case not found or access denied")

    # Idempotency Check first
    idempotency_key = generate_idempotency_key(document_id, pipeline_version)
    existing_job = db.query(DocumentProcessingJob).filter(
        DocumentProcessingJob.idempotency_key == idempotency_key
    ).first()

    if existing_job:
        return existing_job, False

    # 3. Eligibility Gate (for new jobs)
    if document.scan_status != ScanStatus.CLEAN:
        raise IntelligenceError("DOC_NOT_ELIGIBLE", "Document has not passed malware scan")

    if document.processing_status not in (
        EvidenceProcessingStatus.READY_FOR_OCR,
        EvidenceProcessingStatus.OCR_FAILED,
        EvidenceProcessingStatus.REPROCESS_REQUESTED
    ):
        raise IntelligenceError("DOC_NOT_ELIGIBLE", f"Invalid lifecycle state: {document.processing_status}")

    from app.models.shared import ProcessingState
    if case.processing_state not in [ProcessingState.AWAITING_EVIDENCE, ProcessingState.EVIDENCE_READY]:
        raise IntelligenceError("DOC_NOT_ELIGIBLE", f"Case state {case.processing_state} does not permit processing")

    # Integrity Gate stub - hash validation occurs inside the worker when retrieving object
    if not document.object_key or not document.sha256:
        raise IntelligenceError("HASH_MISMATCH", "Document missing integrity artifacts")

    # 4. Idempotent Job Creation
    idempotency_key = generate_idempotency_key(document_id, pipeline_version)

    # Create new job
    job = DocumentProcessingJob(
        document_id=document.document_id,
        case_id=case.case_id,
        merchant_id=merchant_id,
        job_type="OCR_AND_EXTRACT",
        status="QUEUED",
        pipeline_version=pipeline_version,
        idempotency_key=idempotency_key,
        queued_at=datetime.now(timezone.utc)
    )

    try:
        db.add(job)
        
        # Update document status
        document.processing_status = EvidenceProcessingStatus.OCR_QUEUED
        
        # Audit Log
        import json
        audit = AuditLog(
            case_id=case.case_id,
            user_id=user_id if user_id else None,
            action="DOCUMENT_PROCESSING_REQUESTED",
            details=json.dumps({"document_id": str(document_id), "job_id": str(job.job_id), "pipeline_version": pipeline_version})
        )
        db.add(audit)
        
        db.commit()
        db.refresh(job)
        return job, True
    except IntegrityError:
        # Race condition fallback
        db.rollback()
        existing_job = db.query(DocumentProcessingJob).filter(
            DocumentProcessingJob.idempotency_key == idempotency_key
        ).first()
        if existing_job:
            return existing_job, False
        raise

def get_processing_status(db: Session, document_id: UUID, merchant_id: UUID) -> DocumentProcessingJob:
    """
    Gets the latest processing job for a document, scoped by tenant.
    """
    document = db.query(EvidenceDocument).filter(
        EvidenceDocument.document_id == document_id,
        EvidenceDocument.merchant_id == merchant_id
    ).first()

    if not document:
        raise IntelligenceError("OBJECT_NOT_FOUND", "Document not found or access denied")

    job = db.query(DocumentProcessingJob).filter(
        DocumentProcessingJob.document_id == document.document_id
    ).order_by(DocumentProcessingJob.queued_at.desc()).first()
    
    if not job:
        raise IntelligenceError("OBJECT_NOT_FOUND", "No processing job found for document")
        
    return job
