import dataclasses
from typing import List, Optional
from datetime import datetime, timezone
from app.models.shared import Case
from app.models.module_a import Dispute
from app.models.module_b import Payment, Order, Shipment, Refund
from app.models.module_c import EvidenceDocument, ScanStatus, EvidenceProcessingStatus
from app.models.module_d import DocumentExtraction, ExtractedField, DocumentQualityAssessment
from app.models.module_e import EvidenceValidationRun, EvidenceRequirementAssessment, EvidenceValidationResult, CrossSourceFieldLink, ERequirementState, EValidationResultState, ERuleSeverity

class FeatureValidationError(Exception):
    pass

@dataclasses.dataclass
class MLFeaturesV1:
    # Evidence Coverage
    required_evidence_coverage: float
    missing_required_count: int
    evidence_count: int

    # Consistency
    amount_match: Optional[bool]
    order_id_match: Optional[bool]
    tracking_match: Optional[bool]
    customer_match_score: Optional[float]

    # Timeline
    timeline_valid: Optional[bool]
    days_delivery_to_dispute: Optional[float]

    # Contradictions
    contradiction_count: int
    high_severity_contradictions: int

    # Document Quality
    avg_ocr_confidence: Optional[float]
    min_ocr_confidence: Optional[float]
    document_quality_score: Optional[float]

    # Context
    reason_code: str
    payment_method: Optional[str]
    dispute_amount: int
    disputed_amount_ratio: Optional[float]
    refund_exists: bool
    shipment_available: bool

    # Deadline
    days_to_deadline: Optional[float]
    
    version: str = "ml_features_v1"

    def __post_init__(self):
        # Validate ranges and types
        if not (0.0 <= self.required_evidence_coverage <= 1.0):
            raise FeatureValidationError(f"required_evidence_coverage must be [0,1], got {self.required_evidence_coverage}")
        if self.missing_required_count < 0:
            raise FeatureValidationError("missing_required_count must be >= 0")
        if self.evidence_count < 0:
            raise FeatureValidationError("evidence_count must be >= 0")
            
        if self.customer_match_score is not None and not (0.0 <= self.customer_match_score <= 1.0):
            raise FeatureValidationError(f"customer_match_score must be [0,1], got {self.customer_match_score}")
            
        if self.contradiction_count < 0:
            raise FeatureValidationError("contradiction_count must be >= 0")
        if self.high_severity_contradictions < 0:
            raise FeatureValidationError("high_severity_contradictions must be >= 0")
            
        if self.avg_ocr_confidence is not None and not (0.0 <= self.avg_ocr_confidence <= 1.0):
            raise FeatureValidationError(f"avg_ocr_confidence must be [0,1], got {self.avg_ocr_confidence}")
        if self.min_ocr_confidence is not None and not (0.0 <= self.min_ocr_confidence <= 1.0):
            raise FeatureValidationError(f"min_ocr_confidence must be [0,1], got {self.min_ocr_confidence}")
        if self.document_quality_score is not None and not (0.0 <= self.document_quality_score <= 1.0):
            raise FeatureValidationError(f"document_quality_score must be [0,1], got {self.document_quality_score}")
            
        if self.dispute_amount < 0:
            raise FeatureValidationError("dispute_amount must be >= 0")
        if self.disputed_amount_ratio is not None and self.disputed_amount_ratio < 0:
            raise FeatureValidationError("disputed_amount_ratio must be >= 0")
            
        if not isinstance(self.reason_code, str):
            raise FeatureValidationError("reason_code must be string")

@dataclasses.dataclass
class FeatureBuilderContext:
    case: Case
    dispute: Dispute
    payments: List[Payment]
    orders: List[Order]
    shipments: List[Shipment]
    refunds: List[Refund]
    documents: List[EvidenceDocument]
    extractions: List[DocumentExtraction]
    extracted_fields: List[ExtractedField]
    quality_assessments: List[DocumentQualityAssessment]
    run: Optional[EvidenceValidationRun]
    assessments: List[EvidenceRequirementAssessment]
    results: List[EvidenceValidationResult]
    links: List[CrossSourceFieldLink]

def build_ml_features(context: FeatureBuilderContext, prediction_timestamp: datetime) -> MLFeaturesV1:
    """
    Deterministically builds ML features from the exact Module A-E outputs.
    Preserves UNKNOWN natively as None. Does NOT conflate UNKNOWN with FALSE.
    """
    
    # 1. Evidence Coverage
    active_requirements = [a for a in context.assessments if a.status not in (ERequirementState.UNKNOWN, ERequirementState.UNUSABLE)]
    total_active_req = len(active_requirements)
    present_req = sum(1 for a in active_requirements if a.status == ERequirementState.PRESENT)
    
    required_evidence_coverage = (present_req / total_active_req) if total_active_req > 0 else 0.0
    missing_required_count = sum(1 for a in context.assessments if a.status == ERequirementState.MISSING)
    
    # Valid evidence: cleanly scanned, not rejected/corrupted/failed
    invalid_processing_statuses = [
        EvidenceProcessingStatus.REJECTED, EvidenceProcessingStatus.CORRUPTED, 
        EvidenceProcessingStatus.SCAN_FAILED, EvidenceProcessingStatus.OCR_FAILED,
        EvidenceProcessingStatus.QUARANTINED
    ]
    evidence_count = sum(
        1 for d in context.documents 
        if d.scan_status == ScanStatus.CLEAN and d.processing_status not in invalid_processing_statuses
    )

    # 2. Consistency
    amount_match = None
    order_id_match = None
    tracking_match = None
    customer_match_score = None
    
    for link in context.links:
        if link.semantic_field == "amount":
            amount_match = (link.link_status == "MATCH") if link.link_status != "UNKNOWN" else None
        elif link.semantic_field == "order_id":
            order_id_match = (link.link_status == "MATCH") if link.link_status != "UNKNOWN" else None
        elif link.semantic_field == "tracking":
            tracking_match = (link.link_status == "MATCH") if link.link_status != "UNKNOWN" else None
        elif link.semantic_field == "customer":
            customer_match_score = float(link.match_score) if link.match_score is not None else None

    # 3. Timeline
    timeline_valid = None
    for res in context.results:
        rule_code = res.rule_version.rule.rule_code if res.rule_version and res.rule_version.rule else ""
        if rule_code == "DELIVERY_BEFORE_DISPUTE":
            if res.result == EValidationResultState.PASS:
                timeline_valid = True
            elif res.result == EValidationResultState.FAIL:
                timeline_valid = False
            # UNKNOWN/WARN/NA means timeline_valid remains None
            
    days_delivery_to_dispute = None
    if context.dispute and context.dispute.dispute_created_at:
        delivery_timestamp = None
        # Try to find delivery timestamp from shipments
        for shipment in context.shipments:
            if shipment.delivery_at:
                delivery_timestamp = shipment.delivery_at
                break
        
        # Or from extracted fields (simplification for proof; rely on canonical module B/D output)
        if not delivery_timestamp:
            for field in context.extracted_fields:
                if field.field_name == "delivery_date" and field.timestamp_value:
                    delivery_timestamp = field.timestamp_value
                    break
                    
        if delivery_timestamp:
            # Positive = delivery happened BEFORE dispute
            diff = (context.dispute.dispute_created_at - delivery_timestamp).total_seconds() / 86400.0
            days_delivery_to_dispute = diff

    # 4. Contradictions
    contradiction_count = sum(1 for res in context.results if res.result == EValidationResultState.FAIL)
    high_severity_contradictions = sum(1 for res in context.results if res.result == EValidationResultState.FAIL and res.severity == ERuleSeverity.ERROR)

    # 5. Document Quality
    confidences = [float(f.field_confidence) for f in context.extracted_fields if f.field_confidence is not None]
    avg_ocr_confidence = (sum(confidences) / len(confidences)) if confidences else None
    min_ocr_confidence = min(confidences) if confidences else None
    
    document_quality_score = None
    if context.quality_assessments:
        qs = [float(q.quality_score) for q in context.quality_assessments if q.quality_score is not None]
        if qs:
            document_quality_score = sum(qs) / len(qs)
            
    # 6. Context
    reason_code = context.dispute.reason_code if context.dispute else "UNKNOWN"
    payment_method = context.payments[0].method if context.payments and context.payments[0].method else None
    dispute_amount = context.dispute.amount_minor if context.dispute else 0
    
    disputed_amount_ratio = None
    if context.dispute and context.orders and context.orders[0].order_amount_minor:
        order_amt = context.orders[0].order_amount_minor
        if order_amt > 0:
            disputed_amount_ratio = float(context.dispute.amount_minor) / float(order_amt)
            
    refund_exists = len(context.refunds) > 0
    shipment_available = len(context.shipments) > 0

    # 7. Deadline
    days_to_deadline = None
    if context.dispute and context.dispute.respond_by:
        respond_by = context.dispute.respond_by
        if respond_by.tzinfo is None:
            respond_by = respond_by.replace(tzinfo=timezone.utc)
        pred_tz = prediction_timestamp
        if pred_tz.tzinfo is None:
            pred_tz = pred_tz.replace(tzinfo=timezone.utc)
        days_to_deadline = (respond_by - pred_tz).total_seconds() / 86400.0

    return MLFeaturesV1(
        required_evidence_coverage=required_evidence_coverage,
        missing_required_count=missing_required_count,
        evidence_count=evidence_count,
        amount_match=amount_match,
        order_id_match=order_id_match,
        tracking_match=tracking_match,
        customer_match_score=customer_match_score,
        timeline_valid=timeline_valid,
        days_delivery_to_dispute=days_delivery_to_dispute,
        contradiction_count=contradiction_count,
        high_severity_contradictions=high_severity_contradictions,
        avg_ocr_confidence=avg_ocr_confidence,
        min_ocr_confidence=min_ocr_confidence,
        document_quality_score=document_quality_score,
        reason_code=reason_code,
        payment_method=payment_method,
        dispute_amount=dispute_amount,
        disputed_amount_ratio=disputed_amount_ratio,
        refund_exists=refund_exists,
        shipment_available=shipment_available,
        days_to_deadline=days_to_deadline
    )
