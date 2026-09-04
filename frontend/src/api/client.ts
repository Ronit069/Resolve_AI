import type {
  ObservabilityMetricsResponse,
  QueueListingResponse,
  AuditFeedResponse,
  CaseWorkspaceResponse,
  DraftResponse,
  ReviewActionRequest,
  ReviewActionResponse,
  ApiErrorBody,
  ModelEvaluationResponse,
} from "./types";

// Development-mode identity is attached as a plain X-User-Id header on
// every request. This is NOT a credential and is never sent as a cookie —
// see docs/... Module I blueprint §7 and the frozen PO decision: X-User-Id
// is a development-only identity mechanism, not production authentication.
let currentUserId: string | null = null;

export function setApiIdentity(userId: string | null) {
  currentUserId = userId;
}

const API_BASE_URL: string =
  (import.meta as { env?: { VITE_API_BASE_URL?: string } }).env?.VITE_API_BASE_URL ??
  "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (currentUserId) {
    headers["X-User-Id"] = currentUserId;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const body: ApiErrorBody = await response.json();
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail) && body.detail.length > 0) {
        detail = body.detail.map((d) => d.msg).join("; ");
      }
    } catch {
      // response body wasn't JSON; keep the generic message
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

// ---- Existing H/G/H-22 contracts (consumed as-is, never modified) ----

export function getQueueMetrics(): Promise<ObservabilityMetricsResponse> {
  return request("/api/v1/observability/queue-metrics");
}

export function getCaseWorkspace(caseId: string): Promise<CaseWorkspaceResponse> {
  return request(`/api/v1/cases/${caseId}/workspace`);
}

export function getCurrentDraft(caseId: string): Promise<DraftResponse> {
  return request(`/api/v1/cases/${caseId}/draft`);
}

export function generateDraft(caseId: string): Promise<DraftResponse> {
  return request(`/api/v1/cases/${caseId}/generate-draft`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function submitReviewAction(
  caseId: string,
  payload: ReviewActionRequest
): Promise<ReviewActionResponse> {
  return request(`/api/v1/cases/${caseId}/review-action`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---- New Module I read surfaces ----

export interface QueueListingParams {
  status?: string;
  limit?: number;
  offset?: number;
  sort?: string;
}

export function listReviewQueue(params: QueueListingParams = {}): Promise<QueueListingResponse> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset != null) query.set("offset", String(params.offset));
  if (params.sort) query.set("sort", params.sort);
  const qs = query.toString();
  return request(`/api/v1/cases/queue${qs ? `?${qs}` : ""}`);
}

export interface AuditFeedParams {
  limit?: number;
  offset?: number;
}

export function getCaseAuditLog(
  caseId: string,
  params: AuditFeedParams = {}
): Promise<AuditFeedResponse> {
  const query = new URLSearchParams();
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset != null) query.set("offset", String(params.offset));
  const qs = query.toString();
  return request(`/api/v1/cases/${caseId}/audit-log${qs ? `?${qs}` : ""}`);
}

// ---- Phase 2: authoritative Step 15 held-out evaluation ----

export function getModelEvaluation(): Promise<ModelEvaluationResponse> {
  return request("/api/v1/observability/model-evaluation");
}
