from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any

from app.core.database import get_db
from app.services.intelligence import request_document_processing, get_processing_status, IntelligenceError
from app.worker.tasks import process_evidence_document_task
from app.models.shared import Merchant

router = APIRouter()

def get_current_merchant(db: Session = Depends(get_db)):
    merchant = db.query(Merchant).filter(Merchant.is_active == True).first()
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials / No merchant found",
        )
    return merchant


@router.post("/{document_id}/process", status_code=status.HTTP_202_ACCEPTED)
def process_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
):
    try:
        job, created = request_document_processing(
            db=db,
            document_id=document_id,
            merchant_id=merchant.merchant_id,
            user_id=None, # System/API trigger for now
            pipeline_version="v1.0"
        )

        if created:
            process_evidence_document_task.delay(
                str(job.case_id),
                str(document_id),
                str(merchant.merchant_id),
                str(job.job_id),
                requested_by=None
            )
            
        return {
            "status": "ACCEPTED",
            "job_id": job.job_id,
            "idempotent_reused": not created,
            "pipeline_version": job.pipeline_version
        }
    except IntelligenceError as e:
        status_code = status.HTTP_400_BAD_REQUEST
        if e.code in ["OBJECT_NOT_FOUND", "TENANT_MISMATCH"]:
            status_code = status.HTTP_404_NOT_FOUND
        
        raise HTTPException(
            status_code=status_code,
            detail={
                "error_code": e.code,
                "message": e.message
            }
        )

@router.get("/{document_id}/processing")
def get_document_processing_status(
    document_id: UUID,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
):
    try:
        job = get_processing_status(db, document_id, merchant.merchant_id)
        
        return {
            "document_id": job.document_id,
            "job_id": job.job_id,
            "status": job.status,
            "attempt_no": job.attempt_no,
            "pipeline_version": job.pipeline_version,
            "queued_at": job.queued_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "duration_ms": job.duration_ms,
            "error_code": job.error_code,
            "error_message": job.error_message_masked
        }
    except IntelligenceError as e:
        if e.code == "OBJECT_NOT_FOUND":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)
