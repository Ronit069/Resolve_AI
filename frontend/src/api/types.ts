// Typed mirrors of the backend Pydantic response schemas this frontend
// consumes. Kept intentionally minimal — only fields Module I actually
// renders. Do not add fields the backend does not return.

export type AppUserRole =
  | "MERCHANT_ADMIN"
  | "RISK_ANALYST"
  | "APPROVER"
  | "SYSTEM_WORKER"
  | "MODEL_MAINTAINER";

export type ReviewActionEnum =
  | "APPROVE_CONTEST"
  | "APPROVE_ACCEPT"
  | "REQUEST_MORE_EVIDENCE"
  | "EDIT_DRAFT"
  | "REJECT_RECOMMENDATION"
  | "ESCALATE"
  | "ACCEPT"
  | "REQUEST_MORE";

// ---- H-22 queue metrics (existing, consumed as-is) ----

export interface QueueAgeMetrics {
  active_item_count: number;
  average_age_seconds: number | null;
  min_age_seconds: number | null;
  max_age_seconds: number | null;
}

export interface NearDeadlineMetrics {
  threshold_hours: number;
  near_deadline_count: number;
  expired_count: number;
}

export interface ReviewTurnaroundMetrics {
  completed_item_count: number;
  average_turnaround_seconds: number | null;
  min_turnaround_seconds: number | null;
  max_turnaround_seconds: number | null;
}

export interface ObservabilityMetricsResponse {
  generated_at: string;
  queue_age: QueueAgeMetrics;
  near_deadline: NearDeadlineMetrics;
  review_turnaround: ReviewTurnaroundMetrics;
}

// ---- I-03 queue listing (new) ----

export interface QueueItem {
  case_id: string;
  queue_item_id: string;
  queue_status: string;
  priority_score: number;
  respond_by: string;
  dispute_amount_minor: number;
  dispute_currency: string;
  dispute_reason_code: string;
  dispute_status: string;
  recommendation: string | null;
  hard_block: boolean | null;
}

export interface QueueListingResponse {
  items: QueueItem[];
  total_count: number;
  limit: number;
  offset: number;
}

// ---- I-07 audit feed (new) ----

export interface ActivityEvent {
  event_type: "AUDIT_LOG" | "REVIEW_ACTION";
  event_id: string;
  case_id: string;
  actor_user_id: string | null;
  action: string;
  details: string | null;
  created_at: string;
}

export interface AuditFeedResponse {
  items: ActivityEvent[];
  total_count: number;
  limit: number;
  offset: number;
}

// ---- H-02 case workspace (existing, consumed as-is; only the fields
// this UI renders are typed here) ----

export interface CaseWorkspaceResponse {
  case: { case_id: string; merchant_id: string; processing_state: string };
  dispute: {
    amount_minor: number;
    reason_code: string;
    status: string;
    respond_by: string | null;
  };
  queue_item: {
    id: string;
    case_id: string;
    priority_score: number;
    queue_status: string;
    respond_by: string;
  } | null;
  risk_prediction: {
    prediction_id: string;
    calibrated_probability: number;
    recommendation: string;
    hard_block: boolean;
    explanations: Array<{
      explanation_id: string;
      feature_name: string;
      shap_value: number | null;
      display_text: string | null;
    }>;
  } | null;
  evidence_documents: Array<{
    document_id: string;
    object_key: string;
    original_filename: string | null;
    mime_type: string;
  }>;
  uncertainty_warnings: Array<{ source: string; type: string; message: string }>;
}

// ---- Module G draft (existing, consumed as-is) ----

export interface DraftResponse {
  draft_id: string;
  case_id: string;
  guardrail_status: string;
  summary: string;
  contest_amount_minor: string | null;
  draft_json: Record<string, unknown>;
  created_at: string;
}

// ---- H-03 review action (existing, consumed as-is) ----

export interface ReviewActionRequest {
  action: ReviewActionEnum;
  override_reason_code?: string;
  notes?: string;
  draft_revision_json?: Record<string, unknown>;
}

export interface ReviewActionResponse {
  id: string;
  queue_item_id: string;
  case_id: string;
  reviewer_id: string;
  action: ReviewActionEnum;
  override_reason_code: string | null;
  notes: string | null;
  created_at: string;
  dual_approval_status:
    | "AWAITING_SECOND_APPROVAL"
    | "FINALIZED"
    | "ESCALATED_CANCELLED"
    | null;
}

export interface ApiErrorBody {
  detail?: string | Array<{ msg: string }>;
}
