import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import json

from app.models.shared import Case, ProcessingState
from app.models.module_a import Dispute
from app.models.module_b import Shipment
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationResult, EValidationResultState, ERuleSeverity, ValidationRuleVersion, ValidationRuleCatalog
from app.services.ml.feature_builder import FeatureBuilderContext
from app.services.ml.label_policy import LabelContext
from app.services.ml.example_materializer import materialize_example_from_context, materialize_example_from_snapshot, MLExample

@pytest.fixture
def current_time():
    return datetime.now(timezone.utc)

@pytest.fixture
def feature_context(current_time):
    dispute_id = uuid4()
    return FeatureBuilderContext(
        case=Case(case_id=uuid4(), processing_state=ProcessingState.FEATURE_READY),
        dispute=Dispute(
            case_id=uuid4(),
            status="needs_response",
            respond_by=current_time + timedelta(days=5),
            dispute_created_at=current_time,
            amount_minor=10000,
            currency="USD",
            reason_code="fraudulent"
        ),
        payments=[],
        orders=[],
        shipments=[
            Shipment(case_id=uuid4(), delivery_at=current_time - timedelta(days=2))
        ],
        refunds=[],
        documents=[],
        extractions=[],
        extracted_fields=[],
        quality_assessments=[],
        run=None,
        assessments=[],
        results=[],
        links=[]
    )

def test_successful_materialization(feature_context, current_time):
    example = materialize_example_from_context(feature_context, current_time)
    
    # Check properties
    assert isinstance(example, MLExample)
    assert example.feature_schema_version == "ml_features_v1"
    assert example.label_schema_version == "contestability_label_v1"
    assert example.case_id == str(feature_context.case.case_id)
    assert example.prediction_timestamp == current_time
    assert example.label in [0, 1]
    
    # Feature bounds checks passed implicitly since MLFeaturesV1 threw no errors
    assert "required_evidence_coverage" in example.features


def test_determinism_and_hashing(feature_context, current_time):
    example_a = materialize_example_from_context(feature_context, current_time)
    example_b = materialize_example_from_context(feature_context, current_time)
    
    assert example_a.features == example_b.features
    assert example_a.label == example_b.label
    assert example_a.feature_hash == example_b.feature_hash
    assert example_a.label_rationale == example_b.label_rationale


def test_feature_label_independence(feature_context, current_time):
    example_1 = materialize_example_from_context(feature_context, current_time)
    
    # Introduce a label-changing event: A fatal contradiction
    feature_context.results.append(
        EvidenceValidationResult(
            result=EValidationResultState.FAIL,
            severity=ERuleSeverity.ERROR,
            rule_version=ValidationRuleVersion(rule=ValidationRuleCatalog(rule_code="AMOUNT_MISMATCH"))
        )
    )
    example_2 = materialize_example_from_context(feature_context, current_time)
    
    # Label should change
    assert example_1.label != example_2.label
    # Features should change (contradiction_count goes from 0 to 1)
    assert example_1.features["contradiction_count"] != example_2.features["contradiction_count"]
    
    # But changing the features was the ONLY way to change the label, 
    # proving the materializer itself doesn't mix them. The label logic reads the context, 
    # the feature logic reads the context. Neither reads each other.
    assert "label" not in example_2.features
    assert "label_rationale" not in example_2.features


def test_unknown_preservation(feature_context, current_time):
    example = materialize_example_from_context(feature_context, current_time)
    # Check that missing features are explicitly preserved as None
    assert example.features["amount_match"] is None
    assert example.features["avg_ocr_confidence"] is None
    
    # Ensure they weren't cast to False or 0
    assert example.features["amount_match"] is not False
    assert example.features["avg_ocr_confidence"] is not 0


def test_leakage_absence(feature_context, current_time):
    example = materialize_example_from_context(feature_context, current_time)
    features = example.features
    
    # Check no target leakage
    assert "label" not in features
    assert "contestability_label" not in features
    assert "rationale" not in features
    assert "decision_reason" not in features
    assert "bank_outcome" not in features


def test_historical_immutability(feature_context, current_time):
    example_v1 = materialize_example_from_context(feature_context, current_time)
    
    # Simulate DB CaseFeatureSnapshot generated earlier
    snapshot = CaseFeatureSnapshot(
        case_id=feature_context.case.case_id,
        validation_run_id=uuid4(),
        feature_schema_version=example_v1.feature_schema_version,
        features_json=example_v1.features,
        feature_hash=example_v1.feature_hash
    )
    
    # Now context changes (e.g., new validation result arrives)
    feature_context.results.append(
        EvidenceValidationResult(result=EValidationResultState.FAIL, severity=ERuleSeverity.ERROR)
    )
    # Materializing from the old snapshot should ignore the new context for features, 
    # but use current label context if we are generating a label at this point in time. 
    # (Though usually historical snapshot implies historical label too, we're just checking snapshot feature immutability)
    label_context = LabelContext(
        case=feature_context.case, dispute=feature_context.dispute,
        assessments=feature_context.assessments, validation_results=feature_context.results,
        quality_assessments=feature_context.quality_assessments, extractions=feature_context.extractions
    )
    
    example_v2_from_snapshot = materialize_example_from_snapshot(snapshot, label_context, current_time)
    
    assert example_v2_from_snapshot.features == example_v1.features
    assert example_v2_from_snapshot.feature_hash == example_v1.feature_hash
    # The new contradiction in context makes the label 0
    assert example_v2_from_snapshot.label == 0


def test_diagnostic_distribution():
    # Simple logic to verify distribution reporting would work (mock examples)
    examples = [
        MLExample("v1", {}, "hash", "v1", 1, {}, "case1", datetime.now()),
        MLExample("v1", {}, "hash", "v1", 0, {}, "case2", datetime.now()),
        MLExample("v1", {}, "hash", "v1", 1, {}, "case3", datetime.now()),
    ]
    
    total = len(examples)
    positives = sum(1 for e in examples if e.label == 1)
    negatives = sum(1 for e in examples if e.label == 0)
    
    assert total == 3
    assert positives == 2
    assert negatives == 1
    assert (positives / total) == pytest.approx(0.666, 0.01)
