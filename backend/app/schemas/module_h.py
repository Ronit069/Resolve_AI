from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

class H02CaseSchema(BaseModel):
    case_id: UUID
    merchant_id: UUID
    processing_state: str
    model_config = ConfigDict(from_attributes=True)

class H02DisputeSchema(BaseModel):
    amount_minor: int
    reason_code: str
    status: str
    respond_by: Optional[datetime]
    model_config = ConfigDict(from_attributes=True)

class H02QueueItemSchema(BaseModel):
    id: UUID
    case_id: UUID
    priority_score: float
    queue_status: str
    respond_by: datetime
    model_config = ConfigDict(from_attributes=True)

class H02FeatureSnapshotSchema(BaseModel):
    id: UUID
    features_json: Dict[str, Any]
    is_current: bool
    model_config = ConfigDict(from_attributes=True)

class H02ValidationResultSchema(BaseModel):
    result: str
    severity: str
    explanation: Optional[str]
    model_config = ConfigDict(from_attributes=True)

class H02ValidationFindingsSchema(BaseModel):
    status: str
    results: List[H02ValidationResultSchema]

class H02PredictionExplanationSchema(BaseModel):
    explanation_id: UUID
    prediction_id: UUID
    feature_name: str
    shap_value: Optional[float]
    display_text: Optional[str]
    model_config = ConfigDict(from_attributes=True)

class H02RiskPredictionSchema(BaseModel):
    prediction_id: UUID
    calibrated_probability: float
    recommendation: str
    hard_block: bool
    explanations: List[H02PredictionExplanationSchema] = []
    model_config = ConfigDict(from_attributes=True)

class H02DraftClaimSchema(BaseModel):
    claim_text: str
    support_status: str
    fact_refs: Optional[List[str]] = None
    model_config = ConfigDict(from_attributes=True)

class H02GuardrailResultSchema(BaseModel):
    check_type: str
    result: str
    details_json: Optional[Dict[str, Any]] = None
    model_config = ConfigDict(from_attributes=True)

class H02GeneratedDraftSchema(BaseModel):
    id: UUID
    summary: str
    draft_json: Dict[str, Any]
    guardrail_status: str
    is_current: bool
    claims: List[H02DraftClaimSchema] = []
    guardrail_results: List[H02GuardrailResultSchema] = []
    model_config = ConfigDict(from_attributes=True)

class H02EvidenceDocumentSchema(BaseModel):
    document_id: UUID
    object_key: str
    original_filename: Optional[str]
    mime_type: str
    model_config = ConfigDict(from_attributes=True)

class H02UncertaintyWarningSchema(BaseModel):
    source: str
    type: str
    message: str

class CaseWorkspaceResponse(BaseModel):
    case: H02CaseSchema
    dispute: H02DisputeSchema
    queue_item: Optional[H02QueueItemSchema] = None
    feature_snapshot: Optional[H02FeatureSnapshotSchema] = None
    evidence_findings: Optional[H02ValidationFindingsSchema] = None
    risk_prediction: Optional[H02RiskPredictionSchema] = None
    current_draft: Optional[H02GeneratedDraftSchema] = None
    evidence_documents: List[H02EvidenceDocumentSchema] = []
    uncertainty_warnings: List[H02UncertaintyWarningSchema] = []

from app.models.module_h import ReviewActionEnum

class ReviewActionCreateRequest(BaseModel):
    action: ReviewActionEnum
    override_reason_code: Optional[str] = None
    notes: Optional[str] = None
    draft_revision_json: Optional[Dict[str, Any]] = None

class ReviewActionResponse(BaseModel):
    id: UUID
    queue_item_id: UUID
    case_id: UUID
    reviewer_id: UUID
    action: ReviewActionEnum
    override_reason_code: Optional[str] = None
    notes: Optional[str] = None
    draft_revision_json: Optional[Dict[str, Any]] = None
    created_at: datetime
    # H-05 Dual Control: "AWAITING_SECOND_APPROVAL" | "FINALIZED" | "ESCALATED_CANCELLED" | None
    dual_approval_status: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# H-22 Observability: aggregate-only queue metrics (queue age, cases near
# deadline, review turnaround). Submission success rate, API errors and
# won/lost/closed outcome metrics are deferred (blocked on H-13/H-19) and
# intentionally have no schema here.

class H22QueueAgeMetrics(BaseModel):
    active_item_count: int
    average_age_seconds: Optional[float] = None
    min_age_seconds: Optional[float] = None
    max_age_seconds: Optional[float] = None

class H22NearDeadlineMetrics(BaseModel):
    threshold_hours: int
    near_deadline_count: int
    expired_count: int

class H22ReviewTurnaroundMetrics(BaseModel):
    completed_item_count: int
    average_turnaround_seconds: Optional[float] = None
    min_turnaround_seconds: Optional[float] = None
    max_turnaround_seconds: Optional[float] = None

class H22ObservabilityMetricsResponse(BaseModel):
    generated_at: datetime
    queue_age: H22QueueAgeMetrics
    near_deadline: H22NearDeadlineMetrics
    review_turnaround: H22ReviewTurnaroundMetrics
