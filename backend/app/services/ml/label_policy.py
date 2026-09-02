from typing import List, Optional
from datetime import datetime, timezone
import dataclasses
from uuid import UUID

from app.models.shared import Case, ProcessingState
from app.models.module_a import Dispute
from app.models.module_e import (
    EvidenceValidationRun,
    EvidenceRequirementAssessment,
    EvidenceValidationResult,
    ERequirementState,
    EValidationResultState,
    ERuleSeverity
)
from app.models.module_d import DocumentQualityAssessment, DocumentExtraction

@dataclasses.dataclass
class LabelRationale:
    label: int
    label_policy_version: str
    decision_reason: str
    blocking_reasons: List[str]
    hard_block_indicators: List[str]

@dataclasses.dataclass
class LabelContext:
    case: Case
    dispute: Dispute
    validation_run: Optional[EvidenceValidationRun] = None
    assessments: List[EvidenceRequirementAssessment] = dataclasses.field(default_factory=list)
    validation_results: List[EvidenceValidationResult] = dataclasses.field(default_factory=list)
    quality_assessments: List[DocumentQualityAssessment] = dataclasses.field(default_factory=list)
    extractions: List[DocumentExtraction] = dataclasses.field(default_factory=list)

def generate_contestability_label(context: LabelContext, current_time: Optional[datetime] = None) -> LabelRationale:
    """
    Deterministically generates the contestability label for Module F.
    1 = SAFE_TO_CONTEST
    0 = NOT_SAFE_TO_AUTOMATE
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
        
    blocking_reasons = []
    hard_block_indicators = []
    
    # 1. Invalid Dispute Status (Hard Block)
    invalid_dispute_statuses = ["won", "lost", "closed", "expired", "withdrawn"]
    if context.dispute and context.dispute.status and context.dispute.status.lower() in invalid_dispute_statuses:
        blocking_reasons.append(f"invalid_dispute_status: {context.dispute.status}")
        hard_block_indicators.append("invalid_dispute_status")
        
    # 2. Deadline Expired (Hard Block)
    if context.dispute and context.dispute.respond_by:
        respond_by = context.dispute.respond_by
        if respond_by.tzinfo is None:
            respond_by = respond_by.replace(tzinfo=timezone.utc)
        curr_tz = current_time
        if curr_tz.tzinfo is None:
            curr_tz = curr_tz.replace(tzinfo=timezone.utc)
        days_to_deadline = (respond_by - curr_tz).total_seconds() / 86400.0
        if days_to_deadline <= 0:
            blocking_reasons.append("deadline_expired")
            hard_block_indicators.append("deadline_expired")
    
    # 3. Missing Mandatory Evidence (Hard Block)
    has_missing_evidence = False
    has_unusable_evidence = False
    has_unknown_evidence = False
    for assessment in context.assessments:
        if assessment.requirement_level == "MANDATORY":
            if assessment.status == ERequirementState.MISSING:
                has_missing_evidence = True
                blocking_reasons.append(f"mandatory_evidence_missing: {assessment.evidence_type}")
            elif assessment.status == ERequirementState.UNUSABLE:
                has_unusable_evidence = True
                blocking_reasons.append(f"mandatory_evidence_unusable: {assessment.evidence_type}")
            elif assessment.status == ERequirementState.UNKNOWN:
                has_unknown_evidence = True
                blocking_reasons.append(f"mandatory_evidence_unknown: {assessment.evidence_type}")
                
    if has_missing_evidence or has_unusable_evidence:
        hard_block_indicators.append("mandatory_evidence_missing")
        
    # 4. Fatal Contradictions (Hard Block)
    has_fatal_contradiction = False
    has_unresolved_material_contradiction = False
    has_unknown_rule_result = False
    for res in context.validation_results:
        rule_code = "unknown_rule"
        if res.rule_version and res.rule_version.rule:
            rule_code = res.rule_version.rule.rule_code

        if res.result == EValidationResultState.FAIL:
            if res.severity == ERuleSeverity.ERROR:
                has_fatal_contradiction = True
                blocking_reasons.append(f"fatal_contradiction: {rule_code}")
            elif res.severity == ERuleSeverity.WARN:
                # Material contradiction unresolved
                has_unresolved_material_contradiction = True
                blocking_reasons.append(f"unresolved_material_contradiction: {rule_code}")
        elif res.result == EValidationResultState.UNKNOWN:
            has_unknown_rule_result = True
            blocking_reasons.append(f"unknown_rule_result: {rule_code}")

    if has_fatal_contradiction:
        hard_block_indicators.append("fatal_contradiction")
        
    # 5. Insufficient Document Quality
    # If explicitly available, check quality constraints. 
    # Ambiguous or degraded quality -> label 0
    for q in context.quality_assessments:
        if q.quality_score is not None and q.quality_score < 0.6:
            blocking_reasons.append("insufficient_document_quality")
    for ext in context.extractions:
        if ext.overall_confidence is not None and ext.overall_confidence < 0.7:
            blocking_reasons.append("ambiguous_ocr_confidence")
            
    # Compile label
    if hard_block_indicators or blocking_reasons or has_unknown_evidence or has_unknown_rule_result or has_unresolved_material_contradiction:
        label = 0
        decision_reason = "NOT_SAFE_TO_AUTOMATE: " + ", ".join(list(set(hard_block_indicators + blocking_reasons[:3])))
    else:
        label = 1
        decision_reason = "SAFE_TO_CONTEST: All mandatory evidence present, rules passed, no fatal contradictions."

    return LabelRationale(
        label=label,
        label_policy_version="contestability_label_v1",
        decision_reason=decision_reason,
        blocking_reasons=blocking_reasons,
        hard_block_indicators=list(set(hard_block_indicators))
    )
