# Module I — Frontend / Merchant & Reviewer Web Application

Status: DRAFT — for Product Owner review. Not authorized for implementation.
Continuation note: this letter continues the repository's own A-H sequence. It does **not** correspond to the master `ResolveAI_Implementation_Blueprint.md`'s own "Module I — Cost-Sensitive Evaluation," which is already implemented inside the repository's Module F. See `ResolveAI_Next_Four_Modules_Research_Report.md` §2 for the full mapping and the ambiguity this creates.

## 1. Objective

Give the roles already defined by the project's own requirements (Merchant Admin, Risk Analyst, Approver) a working web UI over the existing, stable, already-tested backend built in Modules A-H, so a human can actually use the system end-to-end instead of only via direct HTTP calls or pytest.

## 2. Scope

**Module boundary (revised per PO decision):** Frontend / Application module, with only explicitly authorized thin backend read-surface additions required to support the specified UI screens. This module is not "frontend-only" in the sense of forbidding all backend work — it is frontend-plus-the-minimum-narrow-read-surfaces its own seven screens cannot function without, and nothing beyond that.

In scope:
- (a) All read/display screens over existing endpoints, and the human-decision screens that submit to the existing `POST /api/v1/cases/{case_id}/review-action` endpoint — these consume backend contracts that already exist today, unchanged.
- (b) Three narrow, explicitly-scoped, **read-only** backend additions, each required only because its corresponding screen cannot be built without it (detailed in §6): a dispute-queue listing endpoint (I-03), a held-out model-metrics read endpoint (I-06), and an audit/activity read endpoint (I-07).

Out of scope: any backend surface beyond those three named read-only additions (no general backend development), any new write/action endpoint beyond the one that already exists, any Razorpay-facing capability, any new authentication mechanism, and any screen or action implying backend capability beyond what §6 defines (see §15).

## 3. Authoritative Requirements

| ID (this draft) | Requirement | Source |
|---|---|---|
| I-01 | Login / role landing showing user role and merchant context | `ResolveAI_Implementation_Blueprint.md` §9, row "Login / role landing" |
| I-02 | Risk Command Center: open disputes, amount at risk, deadlines, decision distribution | §9, row "Risk Command Center" |
| I-03 | Dispute Queue: status, amount, reason, deadline, evidence score, recommendation; filter/sort/open | §9, row "Dispute Queue" |
| I-04 | Case Workspace: dispute, timeline, documents, extracted facts, validation, score, explanation | §9, row "Case Workspace"; directly served by existing `GET /api/v1/cases/{case_id}/workspace` (H-02) |
| I-05 | Response Review: grounded claims with evidence references, attachments, warnings; approve/edit/reject | §9, row "Response Review"; directly served by existing `GET /api/v1/cases/{case_id}/draft` (Module G) and `POST .../review-action` (H-03) |
| I-06 | Model Metrics: held-out precision, recall, PR-AUC, confusion matrix, FP cost, calibration | §9, row "Model Metrics" |
| I-07 | Audit / Activity: case transitions, user actions, model/policy versions | §9, row "Audit / Activity" |
| I-08 | Judges/reviewers should see evidence, reasoning, confidence and cost in a single path | §9, "Demo UX priority" callout |
| I-09 | Routes validate/authenticate; services implement use cases; UI does not embed business rules | §14, "Architecture rule" callout (applies to the full stack, frontend included) |

## 4. Functional Requirements

- Render the reviewer queue and case workspace from existing, already-versioned API responses (`H02*` schemas in `app/schemas/module_h.py`) without re-deriving any business rule client-side (e.g., recommendation logic, dual-control eligibility, override-requirement logic all stay server-side, per I-09).
- Submit review actions (`APPROVE_CONTEST`, `APPROVE_ACCEPT`, `REQUEST_MORE_EVIDENCE`, `EDIT_DRAFT`, `REJECT_RECOMMENDATION`, `ESCALATE`) through the existing endpoint, surfacing its existing validation errors (missing override reason/notes, dual-control pending state, etc.) as-is rather than re-implementing that validation in the client.
- Render the H-22 queue-age / near-deadline / review-turnaround metrics from the existing `GET /api/v1/observability/queue-metrics` endpoint for the Risk Command Center screen (I-02).
- Model Metrics screen (I-06) consumes whatever held-out evaluation artifacts Module F's existing training/evaluation scripts already produce (e.g., its reports directory) — this module does not compute new metrics, only displays existing ones. If no stable API for this exists yet, that is a gap this module surfaces rather than silently fabricates (see §18).

## 5. Data Model

- **Existing tables/models consumed (read-only from the frontend's perspective)**: `Case`, `Dispute` (Module A), `ReviewQueueItem`, `ReviewAction` (Module H), `RiskPrediction`, `PredictionExplanation` (Module F), `GeneratedDraft`, `DraftClaim`, `LLMGuardrailResult` (Module G), `EvidenceDocument` (Module C), `EvidenceValidationRun`, `EvidenceValidationResult` (Module E).
- **Required schema changes**: none. This module (frontend plus its three narrow read-only backend additions) adds no new column, table, or constraint anywhere.
- **New tables**: none required by this draft. If real session/JWT authentication is authorized (§7, Open PO Decision), a `sessions`/`refresh_tokens`-style table may be required — explicitly deferred to that PO decision, not assumed here.
- The three narrow backend read additions (§6) query existing tables only (`ReviewQueueItem`/`Case`/`Dispute`/`RiskPrediction` for the queue listing, Module F's existing evaluation artifacts for model metrics, `AuditLog` for the audit feed) and require no new tables or schema changes.
- Do not invent persistence: this module has no legitimate reason to write to the database except through the existing review-action endpoint. The three new read endpoints are `SELECT`-only; none of them writes.

## 6. API / Service Contracts

Four of the seven screens consume contracts that already exist today, unchanged (this module is a **pure consumer** for these). Three screens require a narrow, explicitly-scoped, read-only backend addition that is itself part of this module's defined scope (§2) — not general backend development, and not a separate module's concern:

| Screen | Endpoint | Input | Output | Authorization | Status |
|---|---|---|---|---|---|
| Case Workspace (I-04) | `GET /api/v1/cases/{case_id}/workspace` | `case_id` | `CaseWorkspaceResponse` | `get_current_merchant` (existing) | EXISTING, frozen (H-02) — consumed as-is |
| Review action (I-05) | `POST /api/v1/cases/{case_id}/review-action` | `ReviewActionCreateRequest` | `ReviewActionResponse` | `require_role([APPROVER])` (existing) | EXISTING, frozen (H-03/H-04/H-05/H-18) — consumed as-is |
| Draft display (I-05) | `GET /api/v1/cases/{case_id}/draft` | `case_id` | draft JSON | existing | EXISTING (Module G) — consumed as-is |
| Risk Command Center metrics (I-02) | `GET /api/v1/observability/queue-metrics` | none | `H22ObservabilityMetricsResponse` | `require_role([MERCHANT_ADMIN, RISK_ANALYST, APPROVER])` (existing) | EXISTING, frozen (H-22) — consumed as-is |
| Dispute Queue (I-03) | new `GET` queue-listing endpoint | filter/pagination params | list of queue-item summaries (status, amount, reason, deadline, evidence score, recommendation) | reuse existing `get_current_merchant` + `require_role([...])` conventions | **NEW — narrow, read-only backend addition, in scope for this module.** A read-only `SELECT`-style query over already-existing, already-populated tables (`ReviewQueueItem`, `Case`, `Dispute`, `RiskPrediction`) — no new persistence, no new business rule. Confirmed absent across the entire H-item audit sequence; only single-case `{case_id}` endpoints exist today. |
| Model Metrics (I-06) | new `GET` held-out model-metrics read endpoint | none / model version | Module F's existing evaluation report data (precision/recall/PR-AUC/confusion matrix/FP cost/calibration) | reuse existing RBAC convention | **NEW — narrow, read-only backend addition, in scope for this module.** Surfaces Module F's already-computed evaluation artifacts over HTTP; does not compute any new metric. |
| Audit / Activity (I-07) | new `GET` audit-feed read endpoint | filter/pagination params | `AuditLog` rows | reuse existing RBAC convention | **NEW — narrow, read-only backend addition, in scope for this module.** `AuditLog` already exists and is already written to; only a listing endpoint is missing. |

These three additions are the entirety of this module's backend surface. Building them does not make this "a backend module" — each is a single, narrowly-scoped, read-only query endpoint over data that already exists, required only because its one corresponding screen cannot render without it. This module must not add any endpoint, table, or business rule beyond these three (see §15).

## 7. Security / Authorization

- Reuse the existing RBAC convention (`require_role([...])`, `AppUserRole` enum) exactly as-is; the frontend must never embed its own authorization decision — every gated action must be re-validated server-side (already true of the existing endpoints; the frontend must not assume otherwise).
- Reuse the existing tenant-isolation convention (`get_current_merchant`) — the frontend must never allow a client-side merchant switch to bypass server-side scoping.
- **Open gap, not resolved by this draft**: the current backend authenticates via a bare `X-User-Id` HTTP header with no password/session/token — an MVP convenience, not real authentication. Master blueprint §12 calls for "JWT/session authentication; secure password handling." Building a production-shaped login screen (I-01) against header-based pseudo-auth would be misleading; this is an explicit Open PO Decision (§18), not something this module resolves unilaterally.

## 8. State / Workflow Rules

None owned by this module. All case/queue state transitions remain owned by Modules A/E/F/G/H exactly as implemented; this module only renders their current state and submits already-defined actions.

## 9. Auditability

No new audit requirement — every action this module can trigger already produces its audit trail server-side (`ReviewAction` rows, H-03/H-18). The Audit/Activity screen (I-07) is a read surface over that existing trail, via the narrow listing endpoint addition defined in §6.

## 10. Idempotency / Concurrency

Not applicable to this module beyond standard UI practice (disable a submit button during an in-flight request to avoid duplicate `POST` calls) — all real idempotency guarantees remain server-side and are already implemented (H-03's queue-item locking via `with_for_update()`).

## 11. External Integrations

None. This module talks only to the existing ResolveAI backend API. No third-party contract, documented or undocumented, is implicated.

## 12. Observability

Displays existing H-22 metrics (§6); introduces no new metrics of its own in this draft. Standard frontend error/performance logging is an implementation detail for the eventual plan, not specified here.

## 13. Testing Requirements

(For the eventual implementation plan, not built here.) Component/unit tests for rendering logic; integration tests against the existing backend test fixtures/endpoints (not against production data); explicit tests that the UI never renders an action as available when the backend would reject it (e.g., don't show "Approve Contest" as free of consequence when H-18 would require an override reason) — assertion should be "the UI surfaces the backend's real validation," not "the UI duplicates the backend's validation logic."

## 14. Migration Requirements

None. No backend schema change is required by this module's core scope (§5).

## 15. Non-Goals

This module must NOT:
- Expand beyond the three named, narrow, read-only backend additions in §6 into general backend development. Any backend endpoint, table, or business rule beyond those three (queue listing, model-metrics read, audit-feed read) requires separate authorization and is not part of this module.
- Design or implement any new business workflow. The three backend additions are read-only presentation surfaces over already-existing, already-computed data — they must not introduce any new decision logic, state transition, or workflow of their own.
- Re-implement any business rule that already lives server-side (recommendation logic, dual-control eligibility, override requirements, deadline/amount gating) — the frontend calls the existing endpoints and displays their results/errors.
- Implement any Razorpay-facing action (submit, accept, document upload) — no such backend endpoint exists yet (that is Module J's scope); no "Submit to Razorpay" button may be built ahead of it.
- Display or imply won/lost/closed outcome data — `DisputeOutcome` is unpopulated (Module K's scope); no synthetic/placeholder outcome data may be shown.
- Modify Modules A-H backend code, schema, or tests.
- Modify TEST_HOLDOUT.
- Invent a new authentication mechanism unilaterally, or represent the existing `X-User-Id` header mechanism as production authentication — real session/JWT auth remains a separate, unresolved Open PO Decision (§18), not something this module decides or silently assumes either way.

## 16. Dependencies

- Depends on: Modules A-H (as a consumer of their existing, stable API contracts for four of the seven screens, and as the read-only source of the tables the three narrow backend additions query — `ReviewQueueItem`/`Case`/`Dispute`/`RiskPrediction`, Module F's evaluation artifacts, `AuditLog`).
- Not depended upon by: Modules J, K, L (none require this module to exist first).
- Soft dependency: benefits from Module L's eventual Dockerfile/deployment packaging, but does not require it to be built or tested.

## 17. Acceptance Criteria

- I-04 and I-05 (Case Workspace, Response Review) render real data end-to-end against a running backend and successfully submit at least one of each `ReviewActionEnum` value that a demo scenario requires.
- No frontend code duplicates a business rule already enforced server-side.
- I-03, I-06, and I-07 are implementable within this module's own defined scope once their three narrow, read-only backend additions (§6) are built — they are not blocked on a separate module or a separate PO authorization outside this blueprint.
- No code in this module — frontend or the three narrow backend additions — implements any endpoint, table, or business rule beyond what §2 and §6 define (no general backend development).
- Tenant isolation and RBAC are visibly respected (a non-APPROVER role cannot trigger the review-action submit path in the UI, mirroring — not replacing — the server-side 403); the three new read endpoints reuse the existing RBAC/tenant-isolation convention rather than inventing a new one.

## 18. Open PO Decisions

Item 3 from the prior draft (whether the three backend read endpoints belong to this module's scope) is resolved by this revision — see §2, §6. Remaining open decisions:

1. Authentication model: keep MVP header-based pseudo-auth, or build real session/JWT auth first (master §12)? This module does not decide this either way (§15) — it consumes whichever mechanism is authorized.
2. Technology stack: React/Next.js (master Appendix B recommendation) or PO preference?
3. Priority order among the seven screens for a first iteration, if not all seven are to be built at once.
