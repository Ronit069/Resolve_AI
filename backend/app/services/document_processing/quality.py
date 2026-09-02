from typing import List, Dict, Any, Optional
from app.models.module_d import DocumentExtraction, ExtractedField, DocumentQualityAssessment
from app.services.document_processing.schema_registry import schema_registry

class DeterministicQualityAssessor:
    def assess_document(
        self,
        job_id: Any,
        document_id: Any,
        pages: List[Any],
        extraction: DocumentExtraction,
        extracted_fields: List[ExtractedField],
        text_chunks: List[str]
    ) -> DocumentQualityAssessment:
        
        quality_flags = []
        
        # 1. Page level / OCR signals
        empty_pages = 0
        total_text_length = sum(len(t) for t in text_chunks)
        for chunk in text_chunks:
            if len(chunk.strip()) < 10:
                empty_pages += 1
                
        if empty_pages > 0:
            quality_flags.append("EMPTY_PAGE_DETECTED")
            
        ocr_coverage = 1.0 if total_text_length > 50 else 0.2
        if ocr_coverage < 0.5:
            quality_flags.append("LOW_OCR_COVERAGE")
            
        # 2. Classification confidence
        classification_conf = float(extraction.overall_confidence) if extraction.overall_confidence else 0.0
        if classification_conf < 0.7:
            quality_flags.append("LOW_CLASSIFICATION_CONFIDENCE")
            
        # 3. Field completeness & ambiguity & normalization
        schema_info = schema_registry.get_schema_for_type(extraction.detected_document_type)
        expected_fields_count = 3 # Hardcoding MVP expected count, a real system reads the schema
        
        missing_fields = expected_fields_count - len(extracted_fields)
        if missing_fields > 0:
            quality_flags.append("MISSING_REQUIRED_FIELDS")
            
        ambiguous_fields = 0
        low_conf_fields = 0
        for f in extracted_fields:
            if f.review_status == "REVIEW_REQUIRED":
                ambiguous_fields += 1
            if float(f.field_confidence) < 0.8:
                low_conf_fields += 1
                
        if ambiguous_fields > 0:
            quality_flags.append("AMBIGUOUS_FIELDS")
            
        # Calculate Final Confidence (quality_score)
        # Base starts at classification conf
        final_conf = classification_conf
        
        # Penalities
        if missing_fields > 0:
            final_conf -= 0.3
        if ambiguous_fields > 0:
            final_conf -= (0.2 * ambiguous_fields)
        if empty_pages > 0:
            final_conf -= 0.1
        if low_conf_fields > 0:
            final_conf -= 0.1
            
        final_conf = max(0.0, min(1.0, final_conf))
        
        # Determine grade
        if final_conf >= 0.8 and not ambiguous_fields and not missing_fields:
            grade = "GOOD"
        elif final_conf >= 0.5:
            grade = "DEGRADED"
        else:
            grade = "REVIEW_REQUIRED"
            
        # Force review for ambiguity
        if ambiguous_fields > 0 or missing_fields > 0:
            grade = "REVIEW_REQUIRED"
            
        assessment = DocumentQualityAssessment(
            job_id=job_id,
            document_id=document_id,
            ocr_coverage_ratio=ocr_coverage,
            readable_ratio=1.0 if ocr_coverage > 0.5 else 0.5,
            quality_score=final_conf,
            quality_grade=grade,
            quality_flags=quality_flags
        )
        
        return assessment
