import pytest
from app.models.shared import ProcessingState

def test_processing_state_enum():
    assert ProcessingState.RECEIVED.value == "RECEIVED"
    assert ProcessingState.EVIDENCE_READY.value == "EVIDENCE_READY"
    assert len(ProcessingState) == 11

def test_schemas():
    from app.schemas.shared import CaseBase
    case = CaseBase(external_dispute_id="ext-123", source="synthetic")
    assert case.external_dispute_id == "ext-123"
    assert case.source == "synthetic"
