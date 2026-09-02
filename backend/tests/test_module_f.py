import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models.shared import Case, ProcessingState
from app.models.module_a import Dispute
from app.models.module_c import EvidenceDocument
from app.models.module_b import Payment
from app.models.module_e import (
    EvidenceValidationRun,
    EvidenceRequirementAssessment,
    EvidenceValidationResult,
    ERequirementState,
    EValidationResultState,
    ERuleSeverity,
    ValidationRuleVersion,
    ValidationRuleCatalog,
    EvidencePolicyVersion
)
from app.models.module_d import DocumentQualityAssessment, DocumentExtraction
from app.services.ml.label_policy import generate_contestability_label, LabelContext, LabelRationale

@pytest.fixture
def current_time():
    return datetime.now(timezone.utc)

@pytest.fixture
def valid_dispute(current_time):
    return Dispute(
        case_id=uuid4(),
        status="needs_response",
        respond_by=current_time + timedelta(days=5),
        amount_minor=10000,
        currency="USD",
        reason_code="fraudulent"
    )

@pytest.fixture
def valid_case():
    return Case(
        case_id=uuid4(),
        processing_state=ProcessingState.FEATURE_READY
    )

@pytest.fixture
def base_context(valid_case, valid_dispute):
    return LabelContext(
        case=valid_case,
        dispute=valid_dispute,
        assessments=[
            EvidenceRequirementAssessment(
                requirement_level="MANDATORY",
                status=ERequirementState.PRESENT,
                evidence_type="receipt"
            )
        ],
        validation_results=[
            EvidenceValidationResult(
                result=EValidationResultState.PASS,
                severity=ERuleSeverity.INFO,
                rule_version=ValidationRuleVersion(
                    rule=ValidationRuleCatalog(rule_code="AMOUNT_MATCH")
                )
            )
        ],
        quality_assessments=[
            DocumentQualityAssessment(quality_score=0.95)
        ],
        extractions=[
            DocumentExtraction(overall_confidence=0.95)
        ]
    )


def test_positive_case_safe_to_contest(base_context, current_time):
    # All mandatory evidence present, no contradictions, good quality, valid deadline
    rationale = generate_contestability_label(base_context, current_time)
    assert rationale.label == 1
    assert "SAFE_TO_CONTEST" in rationale.decision_reason
    assert not rationale.blocking_reasons
    assert not rationale.hard_block_indicators


def test_negative_missing_mandatory_evidence(base_context, current_time):
    base_context.assessments.append(
        EvidenceRequirementAssessment(
            requirement_level="MANDATORY",
            status=ERequirementState.MISSING,
            evidence_type="tracking_number"
        )
    )
    rationale = generate_contestability_label(base_context, current_time)
    assert rationale.label == 0
    assert "mandatory_evidence_missing: tracking_number" in rationale.blocking_reasons
    assert "mandatory_evidence_missing" in rationale.hard_block_indicators


def test_negative_unusable_evidence(base_context, current_time):
    base_context.assessments[0].status = ERequirementState.UNUSABLE
    rationale = generate_contestability_label(base_context, current_time)
    assert rationale.label == 0
    assert "mandatory_evidence_unusable: receipt" in rationale.blocking_reasons
    assert "mandatory_evidence_missing" in rationale.hard_block_indicators


def test_negative_fatal_contradiction(base_context, current_time):
    base_context.validation_results.append(
        EvidenceValidationResult(
            result=EValidationResultState.FAIL,
            severity=ERuleSeverity.ERROR,
            rule_version=ValidationRuleVersion(
                rule=ValidationRuleCatalog(rule_code="AMOUNT_MISMATCH")
            )
        )
    )
    rationale = generate_contestability_label(base_context, current_time)
    assert rationale.label == 0
    assert "fatal_contradiction: AMOUNT_MISMATCH" in rationale.blocking_reasons
    assert "fatal_contradiction" in rationale.hard_block_indicators


def test_negative_expired_deadline(base_context, current_time):
    base_context.dispute.respond_by = current_time - timedelta(days=1)
    rationale = generate_contestability_label(base_context, current_time)
    assert rationale.label == 0
    assert "deadline_expired" in rationale.blocking_reasons
    assert "deadline_expired" in rationale.hard_block_indicators


def test_negative_invalid_dispute_status(base_context, current_time):
    base_context.dispute.status = "lost"
    rationale = generate_contestability_label(base_context, current_time)
    assert rationale.label == 0
    assert "invalid_dispute_status: lost" in rationale.blocking_reasons
    assert "invalid_dispute_status" in rationale.hard_block_indicators


def test_negative_insufficient_document_quality(base_context, current_time):
    base_context.quality_assessments[0].quality_score = 0.59
    rationale = generate_contestability_label(base_context, current_time)
    assert rationale.label == 0
    assert "insufficient_document_quality" in rationale.blocking_reasons


def test_negative_unresolved_material_unknown(base_context, current_time):
    base_context.validation_results.append(
        EvidenceValidationResult(
            result=EValidationResultState.UNKNOWN,
            severity=ERuleSeverity.ERROR,
            rule_version=ValidationRuleVersion(
                rule=ValidationRuleCatalog(rule_code="DELIVERY_VERIFICATION")
            )
        )
    )
    rationale = generate_contestability_label(base_context, current_time)
    assert rationale.label == 0
    assert "unknown_rule_result: DELIVERY_VERIFICATION" in rationale.blocking_reasons


def test_boundary_exact_deadline(base_context, current_time):
    base_context.dispute.respond_by = current_time
    rationale = generate_contestability_label(base_context, current_time)
    # 0 days remaining should block
    assert rationale.label == 0
    assert "deadline_expired" in rationale.blocking_reasons


def test_ambiguity_borderline(base_context, current_time):
    # Material warning, not full error, should result in 0
    base_context.validation_results.append(
        EvidenceValidationResult(
            result=EValidationResultState.FAIL,
            severity=ERuleSeverity.WARN,
            rule_version=ValidationRuleVersion(
                rule=ValidationRuleCatalog(rule_code="PARTIAL_REFUND_CONFLICT")
            )
        )
    )
    rationale = generate_contestability_label(base_context, current_time)
    assert rationale.label == 0
    assert "unresolved_material_contradiction: PARTIAL_REFUND_CONFLICT" in rationale.blocking_reasons


def test_determinism(base_context, current_time):
    rationale1 = generate_contestability_label(base_context, current_time)
    rationale2 = generate_contestability_label(base_context, current_time)
    
    assert rationale1 == rationale2


def test_leakage_absence():
    # Verify structurally that label generation has no parameters for future outcomes
    import inspect
    sig = inspect.signature(generate_contestability_label)
    params = list(sig.parameters.keys())
    assert "future_outcome" not in params
    assert "reviewer_decision" not in params
    assert "module_g" not in params
    assert "module_h" not in params
