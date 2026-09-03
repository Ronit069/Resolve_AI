"""
Celery tasks for ResolveAI.

enrich_dispute_task: Module B enrichment — replaces the Module A placeholder.
"""

from app.worker.celery_app import celery_app
from app.core.database import SessionLocal
from app.providers.base import ProviderUnavailableError
from app.services.observability.runtime_metrics import track_latency, track_latency_decorator
import logging

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
@track_latency_decorator("queue_duration")
def enrich_dispute_task(self, case_id: str):
    """
    Module B enrichment task. Receives case_id from Module A post-commit dispatch.

    Retry strategy:
      - ProviderUnavailableError → exponential backoff (30s, 60s, 120s), max 3 retries.
      - Terminal errors (not found, validation) → no retry, record ProcessingError.
    """
    logger.info(f"enrich_dispute_task started for case_id={case_id} (attempt {self.request.retries + 1})")

    db = SessionLocal()
    try:
        from app.providers.synthetic import get_synthetic_providers
        from app.services.enrichment import enrich_case

        providers = get_synthetic_providers()
        result = enrich_case(db, case_id, providers)

        logger.info(f"enrich_dispute_task completed for case_id={case_id}: status={result.status}")
        return {"status": result.status, "case_id": case_id, "version": result.version}

    except ProviderUnavailableError as exc:
        db.rollback()
        countdown = 30 * (2 ** self.request.retries)
        logger.warning(
            f"Provider unavailable for case {case_id}, "
            f"retry {self.request.retries + 1}/{self.max_retries} in {countdown}s"
        )
        raise self.retry(exc=exc, countdown=countdown)

    except Exception as exc:
        db.rollback()
        logger.error(f"enrich_dispute_task failed for case_id={case_id}: {exc}", exc_info=True)

        # Record ProcessingError for unexpected failures
        try:
            from app.models.shared import ProcessingError, AuditLog, Case, ProcessingState
            import uuid as uuid_mod
            from datetime import datetime, timezone

            case = db.query(Case).filter(Case.case_id == uuid_mod.UUID(case_id)).first()
            if case and case.processing_state == ProcessingState.ENRICHING:
                # Revert to INGESTED (Correction #1: don't leave stuck in ENRICHING)
                case.processing_state = ProcessingState.INGESTED

                error = ProcessingError(
                    case_id=case.case_id,
                    module="module_b",
                    error_code="ENRICHMENT_UNEXPECTED_ERROR",
                    error_message=str(exc)[:500],
                    retryable=False,
                )
                db.add(error)

                audit = AuditLog(
                    case_id=case.case_id,
                    action="ENRICHMENT_FAILED",
                    details=f"Unexpected error: {str(exc)[:200]}. Case reverted to INGESTED.",
                )
                db.add(audit)

                db.commit()
        except Exception as inner_exc:
            logger.error(f"Failed to record error for case {case_id}: {inner_exc}")
            db.rollback()

        return {"status": "FAILED", "case_id": case_id, "error": str(exc)[:200]}

    finally:
        db.close()

@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
@track_latency_decorator("queue_duration")
def scan_evidence_task(self, document_id: str):
    """
    Background task to scan an uploaded evidence document for malware.
    Transitions document to READY_FOR_OCR or REJECTED/SCAN_FAILED.
    """
    from app.services.scanner import run_evidence_scan
    from app.core.database import SessionLocal
    
    db = SessionLocal()
    try:
        import uuid
        doc_uuid = uuid.UUID(document_id)
        result = run_evidence_scan(db, doc_uuid)
        if not result:
            return "Skipped (not found or not in QUARANTINED state)"
        if result.scan_status == "FAILED":
            # Simulate a retryable error for the deterministic scanner
            raise Exception("Simulated transient scanner failure")
        return f"Scan complete: {result.scan_status}"
    except Exception as exc:
        db.rollback()
        logger.warning(f"Scanner transient failure for {document_id}, retry {self.request.retries + 1}/{self.max_retries}")
        raise self.retry(exc=exc, countdown=10 * (2 ** self.request.retries))
    finally:
        db.close()

@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
@track_latency_decorator("queue_duration")
def process_evidence_document_task(self, case_id: str, document_id: str, merchant_id: str, job_id: str, requested_by: str = None):
    """
    Module D Document Processing Background Task.
    Idempotent execution boundary. Validates document/job relationship.
    """
    from app.core.database import SessionLocal
    from app.models.module_d import DocumentProcessingJob
    from app.models.module_c import EvidenceProcessingStatus, EvidenceDocument
    import uuid

    db = SessionLocal()
    try:
        # Validate job exists and is in a valid state
        job = db.query(DocumentProcessingJob).filter(
            DocumentProcessingJob.job_id == uuid.UUID(job_id),
            DocumentProcessingJob.document_id == uuid.UUID(document_id),
            DocumentProcessingJob.merchant_id == uuid.UUID(merchant_id)
        ).first()

        if not job:
            logger.error(f"Intelligence job {job_id} not found or tenant mismatch")
            return "Failed: Job missing"

        if job.status not in ["QUEUED", "PROCESSING"]:
            logger.info(f"Intelligence job {job_id} already in terminal state {job.status}")
            return f"Skipped: {job.status}"

        document = db.query(EvidenceDocument).filter(
            EvidenceDocument.document_id == uuid.UUID(document_id)
        ).first()

        # Update Job Status to PROCESSING
        job.status = "PROCESSING"
        job.attempt_no = self.request.retries + 1
        job.started_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
        
        document.processing_status = EvidenceProcessingStatus.OCR_PROCESSING
        db.commit()

        from app.services.document_processing.loader import download_and_verify_evidence
        from app.services.document_processing.pdf import PDFProcessor
        from app.services.document_processing.image import ImageProcessor
        from app.services.intelligence import IntelligenceError
        from app.models.shared import AuditLog

        # Step 3 logic:
        try:
            with download_and_verify_evidence(document.object_key, document.sha256) as tmp_file:
                if document.mime_type == "application/pdf":
                    pages = PDFProcessor.process_pdf(db, tmp_file, job.job_id, document.document_id, document.case_id)
                elif document.mime_type in ["image/jpeg", "image/png"]:
                    pages = ImageProcessor.process_image(db, tmp_file, job.job_id, document.document_id, document.case_id, document.mime_type)
                else:
                    raise IntelligenceError("UNSUPPORTED_DOCUMENT_LAYOUT", f"Unsupported mime type: {document.mime_type}")
                
                # Persist pages
                for p in pages:
                    db.add(p)
                db.flush() # Ensure pages get IDs and are available to OCR phase

                # Step 4 logic: OCR and Layout Provenance
                from app.services.document_processing.ocr_adapter import DeterministicOCRAdapter, validate_ocr_result
                import json
                import io
                
                ocr_adapter = DeterministicOCRAdapter()
                full_text_chunks = []
                
                for p in pages:
                    # OCR only if native text isn't used
                    if not p.native_text_used:
                        with track_latency("ocr"):
                            ocr_result = ocr_adapter.perform_ocr(
                                page_artifact_key=p.page_artifact_key,
                                width=p.width_px or 10000,
                                height=p.height_px or 10000,
                                page_number=p.page_number
                            )
                        validate_ocr_result(ocr_result, p.width_px or 10000, p.height_px or 10000)
                        
                        full_text_chunks.append(ocr_result["text"])
                        
                        # Store OCR artifacts privately
                        # 1. OCR Text
                        text_payload = json.dumps({
                            "text": ocr_result["text"],
                            "confidence": ocr_result["confidence"],
                            "source": "ocr",
                            "page_number": p.page_number,
                            # Provide deterministic source hash of text for provenance
                            "text_hash": __import__('hashlib').sha256(ocr_result["text"].encode('utf-8')).hexdigest()
                        }).encode("utf-8")
                        
                        ocr_text_key = f"derived/{document.case_id}/{document.document_id}/{job.job_id}/page_{p.page_number}_text.json"
                        storage_client.upload_file(io.BytesIO(text_payload), ocr_text_key, content_type="application/json")
                        
                        # 2. Layout Blocks
                        layout_payload = json.dumps({
                            "layout_blocks": ocr_result["layout_blocks"],
                            "page_number": p.page_number
                        }).encode("utf-8")
                        
                        layout_key = f"derived/{document.case_id}/{document.document_id}/{job.job_id}/page_{p.page_number}_layout.json"
                        storage_client.upload_file(io.BytesIO(layout_payload), layout_key, content_type="application/json")
                        
                        # Persist references
                        p.ocr_text_object_key = ocr_text_key
                        p.layout_object_key = layout_key
                        db.add(p)
                    else:
                        # Native text was used and uploaded in Step 3
                        # We retrieve it briefly for classification context
                        if p.ocr_text_object_key:
                            resp = storage_client.s3_client.get_object(Bucket=storage_client.bucket, Key=p.ocr_text_object_key)
                            txt_payload = json.loads(resp['Body'].read().decode('utf-8'))
                            full_text_chunks.append(txt_payload.get("text", ""))

                # Step 5 logic: Document Type Detection + Schema Selection
                full_text = " ".join(full_text_chunks)
                
                from app.services.document_processing.classifier import DeterministicClassifier, evaluate_type_match
                from app.services.document_processing.schema_registry import schema_registry
                from app.models.module_d import DocumentModelVersion, DocumentExtraction
                
                classifier = DeterministicClassifier()
                classification_result = classifier.detect_type(full_text)
                
                detected_type = classification_result["detected_document_type"]
                confidence = classification_result["confidence"]
                match_status = evaluate_type_match(document.evidence_type, detected_type, confidence)
                
                schema_info = schema_registry.get_schema_for_type(detected_type)
                schema_version = schema_info["schema_version"] if schema_info else "UNKNOWN"
                
                # Register deterministic model version MVP explicitly
                model_version = db.query(DocumentModelVersion).filter_by(component="CLASSIFIER", active=True).first()
                if not model_version:
                    model_version = DocumentModelVersion(
                        component="CLASSIFIER",
                        model_name="deterministic_rule_based",
                        version="v1.0.0",
                        config_hash="deterministic"
                    )
                    db.add(model_version)
                    db.flush()
                
                # Update job model
                job.extractor_model_version_id = model_version.model_version_id
                
                extraction = DocumentExtraction(
                    job_id=job.job_id,
                    document_id=document.document_id,
                    case_id=document.case_id,
                    expected_evidence_type=document.evidence_type,
                    detected_document_type=detected_type,
                    type_match_status=match_status,
                    extraction_status="PENDING", # Not EXTRACTED yet
                    schema_version=schema_version,
                    overall_confidence=confidence,
                    requires_review=(match_status == "REVIEW_REQUIRED")
                )
                db.add(extraction)
                
                if match_status == "REVIEW_REQUIRED":
                    document.processing_status = EvidenceProcessingStatus.REVIEW_REQUIRED
                    job.status = "COMPLETED"
                    job.completed_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
                    db.commit()
                    return "Review required"
                
                # Step 6 logic: Semantic Field Extraction
                from app.services.document_processing.extractor import DeterministicExtractorFactory
                from app.models.module_d import ExtractedField
                
                extractor = DeterministicExtractorFactory.get_extractor(detected_type)
                
                # Re-fetch layout blocks by page
                layout_blocks_by_page = {}
                for p in pages:
                    if p.layout_object_key:
                        resp = storage_client.s3_client.get_object(Bucket=storage_client.bucket, Key=p.layout_object_key)
                        layout_payload = json.loads(resp['Body'].read().decode('utf-8'))
                        layout_blocks_by_page[p.page_number] = layout_payload.get("layout_blocks", [])
                    elif p.ocr_text_object_key:
                        resp = storage_client.s3_client.get_object(Bucket=storage_client.bucket, Key=p.ocr_text_object_key)
                        txt_payload = json.loads(resp['Body'].read().decode('utf-8'))
                        layout_blocks_by_page[p.page_number] = [{"text": txt_payload.get("text", "")}]
                        
                extracted_fields = extractor.extract_fields([], layout_blocks_by_page, schema_info)
                
                for field_data in extracted_fields:
                    f = ExtractedField(
                        extraction_id=extraction.extraction_id,
                        document_id=document.document_id,
                        field_name=field_data.name,
                        value_type=field_data.value_type,
                        raw_value_masked=field_data.raw_value,
                        field_confidence=field_data.confidence,
                        page_number=field_data.provenance.page_number if field_data.provenance else None,
                        source_bbox=field_data.provenance.bbox if field_data.provenance else None,
                        source_text_hash=field_data.provenance.source_text_hash if field_data.provenance else None,
                        review_status="REVIEW_REQUIRED" if field_data.review_required else "NOT_REQUIRED"
                    )
                    db.add(f)
                
                # Step 7 logic: Normalization
                from app.services.document_processing.normalizer import DeterministicNormalizerRegistry
                
                # Register deterministic model version MVP explicitly for normalization
                norm_model_version = db.query(DocumentModelVersion).filter_by(component="NORMALIZER", active=True).first()
                if not norm_model_version:
                    norm_model_version = DocumentModelVersion(
                        component="NORMALIZER",
                        model_name="deterministic_rule_based_normalizer",
                        version="v1.0.0",
                        config_hash="deterministic"
                    )
                    db.add(norm_model_version)
                    db.flush()
                
                for f in extraction.fields:
                    normalizer = DeterministicNormalizerRegistry.get_normalizer(f.field_name)
                    normalizer.normalize_field(f)
                    
                # Step 8 logic: Document Quality Assessment
                from app.services.document_processing.quality import DeterministicQualityAssessor
                
                # Register deterministic MVP model version
                quality_model_version = db.query(DocumentModelVersion).filter_by(component="QUALITY_ASSESSOR", active=True).first()
                if not quality_model_version:
                    quality_model_version = DocumentModelVersion(
                        component="QUALITY_ASSESSOR",
                        model_name="deterministic_rule_based_quality",
                        version="v1.0.0",
                        config_hash="deterministic"
                    )
                    db.add(quality_model_version)
                    db.flush()
                
                assessor = DeterministicQualityAssessor()
                quality_assessment = assessor.assess_document(
                    job_id=job.job_id,
                    document_id=document.document_id,
                    pages=pages,
                    extraction=extraction,
                    extracted_fields=extraction.fields,
                    text_chunks=full_text_chunks
                )
                db.add(quality_assessment)
                
                extraction.extraction_status = "EXTRACTED"
                
                if quality_assessment.quality_grade == "REVIEW_REQUIRED":
                    document.processing_status = EvidenceProcessingStatus.REVIEW_REQUIRED
                else:
                    document.processing_status = EvidenceProcessingStatus.EXTRACTED
                
                # For Step 8, we successfully completed the entire Module D pipeline!
                job.status = "COMPLETED"
                job.completed_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
                db.commit()
                
                # Step 9 logic: Aggregate Case-Level Intelligence Status
                # We need a fresh transaction context for case-level aggregation to prevent deadlocks
                from app.services.document_processing.aggregator import CaseIntelligenceAggregator
                case_aggregator = CaseIntelligenceAggregator()
                case_aggregator.aggregate_case_status(db, document.case_id)
                db.commit()
                
        except IntelligenceError as ie:
            # Terminal error handling
            db.rollback()
            job.status = "FAILED"
            job.error_code = ie.code
            job.error_message_masked = ie.message
            job.completed_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
            document.processing_status = EvidenceProcessingStatus.OCR_FAILED
            
            import json
            audit = AuditLog(
                case_id=document.case_id,
                user_id=None,
                action="DOCUMENT_PROCESSING_FAILED",
                details=json.dumps({"document_id": str(document.document_id), "job_id": str(job.job_id), "error_code": ie.code})
            )
            db.add(audit)
            db.commit()
            logger.error(f"Intelligence job {job_id} failed terminally: {ie.code}")
            return f"Failed terminally: {ie.code}"

    except IntelligenceError:
        # Already handled above, just return
        return "Failed terminally"
    except Exception as exc:
        db.rollback()
        logger.warning(f"Document processing transient failure for job {job_id}, retry {self.request.retries + 1}/{self.max_retries}")
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))
    finally:
        db.close()
