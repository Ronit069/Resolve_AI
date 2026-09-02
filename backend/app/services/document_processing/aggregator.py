from typing import Any
from sqlalchemy.orm import Session
from app.models.module_c import EvidenceDocument, EvidenceProcessingStatus
from app.models.module_d import CaseDocumentIntelligenceStatus

class CaseIntelligenceAggregator:
    
    def aggregate_case_status(self, db: Session, case_id: Any) -> CaseDocumentIntelligenceStatus:
        """
        Aggregates document-level intelligence statuses into a single case-level summary.
        This must be called within an active transaction.
        """
        # Lock or fetch existing status row
        status_row = db.query(CaseDocumentIntelligenceStatus).filter_by(case_id=case_id).with_for_update().first()
        if not status_row:
            status_row = CaseDocumentIntelligenceStatus(case_id=case_id)
            db.add(status_row)
            
        # Fetch all safe evidence documents for this case (those that passed malware)
        documents = db.query(EvidenceDocument).filter(
            EvidenceDocument.case_id == case_id,
            EvidenceDocument.processing_status.in_([
                EvidenceProcessingStatus.READY_FOR_OCR,
                EvidenceProcessingStatus.OCR_QUEUED,
                EvidenceProcessingStatus.OCR_PROCESSING,
                EvidenceProcessingStatus.EXTRACTED,
                EvidenceProcessingStatus.REVIEW_REQUIRED,
                EvidenceProcessingStatus.OCR_FAILED,
                EvidenceProcessingStatus.REPROCESS_REQUESTED
            ])
        ).all()
        
        status_row.total_safe_documents = len(documents)
        
        queued = 0
        processed = 0
        successful = 0
        review_required = 0
        failed = 0
        
        for doc in documents:
            if doc.processing_status in [EvidenceProcessingStatus.OCR_QUEUED, EvidenceProcessingStatus.OCR_PROCESSING]:
                queued += 1
            elif doc.processing_status == EvidenceProcessingStatus.EXTRACTED:
                successful += 1
                processed += 1
            elif doc.processing_status == EvidenceProcessingStatus.REVIEW_REQUIRED:
                review_required += 1
                processed += 1
            elif doc.processing_status == EvidenceProcessingStatus.OCR_FAILED:
                failed += 1
                
        status_row.queued_documents = queued
        status_row.processed_documents = processed
        status_row.successful_documents = successful
        status_row.review_required_documents = review_required
        status_row.failed_documents = failed
        status_row.blocking_failures = failed
        
        pending = status_row.total_safe_documents - status_row.processed_documents - status_row.failed_documents
        
        if pending > 0 or queued > 0:
            status_row.overall_status = "PENDING"
            status_row.ready_for_module_e = False
        elif failed > 0:
            status_row.overall_status = "FAILED"
            status_row.ready_for_module_e = False
        elif review_required > 0:
            status_row.overall_status = "REVIEW_REQUIRED"
            status_row.ready_for_module_e = False
        else:
            # Everything is extracted/successful
            status_row.overall_status = "COMPLETED"
            status_row.ready_for_module_e = True if status_row.total_safe_documents > 0 else False
            
        return status_row
