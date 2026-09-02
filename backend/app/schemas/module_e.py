from pydantic import BaseModel, Field, UUID4, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.models.module_e import (
    EValidationRunStatus,
    ERuleSeverity,
    EValidationResultState,
    ERequirementState,
    EMatchMethod,
    ELinkStatus,
    EFeatureDataType
)

class EvidenceValidationRunBase(BaseModel):
    id: UUID4
    case_id: UUID4
    evidence_version: str
    policy_version_id: UUID4
    status: EValidationRunStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    created_by: Optional[str] = None
    idempotency_key: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class EvidenceValidationResultBase(BaseModel):
    id: UUID4
    validation_run_id: UUID4
    rule_version_id: UUID4
    result: EValidationResultState
    severity: ERuleSeverity
    source_refs: Optional[Dict[str, Any]] = None
    normalized_values: Optional[Dict[str, Any]] = None
    explanation: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CaseFeatureSnapshotBase(BaseModel):
    id: UUID4
    case_id: UUID4
    validation_run_id: UUID4
    feature_schema_version: str
    features_json: Dict[str, Any]
    feature_hash: str
    created_at: datetime
    is_current: bool

    model_config = ConfigDict(from_attributes=True)


class ModuleFContractSummary(BaseModel):
    required_evidence_coverage: float
    identifier_match_rate: float
    timeline_consistency_score: float
    unknown_field_ratio: float
    contradiction_count: int

    model_config = ConfigDict(from_attributes=True)


class ModuleFContract(BaseModel):
    case_id: UUID4
    validation_run_id: UUID4
    policy_version_id: UUID4
    feature_snapshot_id: UUID4
    findings: List[EvidenceValidationResultBase]
    summary: ModuleFContractSummary
    features: Dict[str, Any]
    status: str

    model_config = ConfigDict(from_attributes=True)
