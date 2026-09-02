# Module K — Outcome Feedback & Training-Label Curation Loop

Status: DRAFT — for Product Owner review. Not authorized for implementation. DEFERRED — hard-blocked on Module J (see §16, §18).
Continuation note: this letter continues the repository's own A-H sequence. It does **not** correspond to the master `ResolveAI_Implementation_Blueprint.md`'s own "Module K — Three-Way Policy Decision," which is already implemented inside the repository's Module F. This module instead completes the E-F-G-H blueprint's own terminal roadmap node, named but not lettered: "-> Outcome Feedback" (line 4), and Module H's own H-19/H-20 items. See `ResolveAI_Next_Four_Modules_Research_Report.md` §2, §7.

## 1. Objective

Ingest real dispute outcome webhooks (won/lost/closed) for disputes actually submitted through Module J, populate `DisputeOutcome`, and implement the explicit curation step — `CuratedFeedbackLabel` — that the frozen H-20 requirement mandates must sit between any outcome and any future ML training dataset.

## 2. Scope

In scope: outcome webhook ingestion, `DisputeOutcome` persistence, and the curation mechanism that sets `CuratedFeedbackLabel.approved_for_training`. Out of scope: any actual retraining pipeline or automatic training-dataset assembly — H-20 explicitly forbids "never automatically retrain from raw webhook outcomes," and the master blueprint marks "Automated retraining/drift alert UI" as OPTIONAL, not required.

## 3. Authoritative Requirements

| ID (this draft) | Requirement | Source |
|---|---|---|
| K-01 | Store won/lost/closed result and link it to prediction, draft, reviewer action and submitted evidence package | H-19 |
| K-02 | Outcome labels enter future ML datasets only through an explicit label curation/versioning step; never automatically retrain from raw webhook outcomes | H-20 |
| K-03 | Later webhooks remain authoritative for under_review/won/lost/closed transitions | H-16 (Clause B already satisfied by Module A/H-06, unaffected; Clause A — outbox reconciliation — is Module J's concern, not this module's) |
| K-04 | Outcome webhooks update the case... and feed outcome labels back into evaluation and later retraining | E-F-G-H blueprint §1.1 step 8 |
| K-05 | Append outcomes to the outcome store rather than mutating historical predictions | Master blueprint §5.15 Module O |
| K-06 | Quality monitoring: feature missingness, score distribution, calibration drift once outcomes exist | Master blueprint §13 (contingent — "once outcomes exist," i.e., after this module) |

## 4. Functional Requirements

- Ingest an outcome webhook event carrying a Razorpay dispute ID and outcome status (won/lost/closed).
- Resolve it to the internal `case_id` and the specific `ContestSubmission` it corresponds to (via `external_dispute_id`/`ContestSubmission.external_dispute_id`) — this resolution is only possible once Module J has produced real `ContestSubmission` rows, which is why this module cannot start before Module J (§16).
- Write a `DisputeOutcome` row (append-only, per K-05) linking `case_id`, `prediction_id` (the `RiskPrediction` that led to the contest), `contest_submission_id`, `outcome`, `amount_deducted_minor`, `source_event_id`, `occurred_at` — using the exact frozen columns already defined in `app/models/module_h.py` (H-9).
- Implement the curation step: a process (workflow, or minimally a reviewable queue) that turns a raw `DisputeOutcome` into a `CuratedFeedbackLabel` row with an explicit `label_quality` (GOLD/SILVER/SYNTHETIC) and `version`, only setting `approved_for_training = True` through that explicit step — never automatically from the webhook handler itself (K-02, non-negotiable per H-20's frozen wording).
- Do not build a training-dataset assembly or retraining trigger in this module — confirmed absent everywhere in the repository (zero `beat_schedule`/periodic-task infrastructure, per the H-20 audit), and explicitly out of scope per K-02 and the master blueprint's OPTIONAL classification.

## 5. Data Model

- **Existing tables/models (frozen, H-00, currently unpopulated)**: `DisputeOutcome` (H9), `CuratedFeedbackLabel` (H10).
- **Required schema changes**: none identified. The H-20 audit (this session) confirmed the existing schema is exactly shaped for this module's needs (`label_quality`, `approved_for_training`, `version` already present).
- **New tables**: none.
- Do not invent persistence beyond H-00's frozen shape; do not add a `MLLabel`/`MLDatasetMember`-writing path in this module — Module F's training pipeline (frozen) is the only legitimate future consumer of curated labels, and this module does not touch Module F.

## 6. API / Service Contracts

| Contract | Direction | Input | Output | Authorization | Validation | Error behavior | Side effects |
|---|---|---|---|---|---|---|---|
| Outcome webhook handler (new endpoint or extension — see Open PO Decisions) | inbound | Razorpay outcome webhook payload | 202-style ack | webhook signature verification, reusing Module A's existing HMAC pattern | resolve to a real `ContestSubmission`; reject/park if unresolvable (do not fabricate a link) | unresolvable outcome recorded for manual reconciliation, never silently dropped or guessed | one `DisputeOutcome` insert |
| Curation action (new, internal) | internal (human- or process-gated) | `outcome_id`, `label_name`, `label_value`, `label_quality` | `CuratedFeedbackLabel` row | reuses existing RBAC convention — likely a new or existing role able to curate labels (undecided, §18) | — | — | one `CuratedFeedbackLabel` insert; `approved_for_training` only settable here, never from the webhook path |

**Do not invent**: no Razorpay outcome-webhook payload shape beyond what §15 of the E-F-G-H blueprint documents ("Dispute webhooks include created, action_required, under_review, won, lost and closed states/events documented by Razorpay").

## 7. Security / Authorization

Reuses Module A's existing webhook signature-verification convention for the inbound side. The curation step needs an explicit authorization decision (§18) — it is a new kind of action (labeling data for ML use) not covered by any existing `AppUserRole` semantics (`MODEL_MAINTAINER` is the closest existing fit but its current usage/scope was never defined beyond the enum itself — confirmed no code branches on it anywhere in the repository).

## 8. State / Workflow Rules

Extends `ContestSubmission.status`/`external_status` (already frozen) rather than introducing a new state machine. `DisputeOutcome` rows are append-only (K-05) — never update a historical row; a corrected/superseding outcome becomes a new row, mirroring the "immutable decision snapshot" principle already established for `RiskPrediction`/`GeneratedDraft` (E-F-G-H blueprint §2.3).

## 9. Auditability

Every `DisputeOutcome` write and every curation action must produce an audit trail entry via the existing `AuditLog`/audit conventions — this is the first module where an outcome could plausibly be disputed/corrected later, making the append-only + audit-trail discipline especially load-bearing.

## 10. Idempotency / Concurrency

Outcome webhooks must be idempotent on `source_event_id` (already a frozen, though not currently unique-constrained, column on `DisputeOutcome` — confirm/flag during implementation planning whether a uniqueness constraint is needed; not decided here to avoid a speculative migration). Reuse Module A's existing webhook-idempotency pattern (`WebhookEvent` table, external event ID) rather than inventing a second one.

## 11. External Integrations

- **Documented**: outcome webhook event types (won/lost/closed/under_review/action_required) per E-F-G-H blueprint §15.
- **Not documented and not needed**: no outbound call is required by this module — it is inbound-only (webhook ingestion) plus the curation step (internal).
- **Integration boundary**: this module consumes webhooks the same way Module A does, but for a different event category (outcome vs. dispute-status) — whether that means extending Module A's existing endpoint or adding a new one is an explicit Open PO Decision (§18), because Module A is frozen and must not be casually redesigned.
- **What must remain deferred**: any automatic retraining trigger (K-02); any change to Module F's training pipeline to consume curated labels — that consumption is Module F's own future concern, not built by this module.

## 12. Observability

Enables the two remaining H-22-adjacent metrics not yet possible (won/lost/closed outcome metrics — explicitly named in the original H-22 requirement and explicitly deferred in the H-22 implementation, pending exactly this module) — additive to the existing `app/services/observability/queue_metrics.py`, not a rework of it.

## 13. Testing Requirements

(For the eventual implementation plan.) Idempotency tests for duplicate outcome webhooks; tests proving `approved_for_training` is never set outside the explicit curation path; tests proving the webhook handler never writes directly to `CuratedFeedbackLabel`; tests for the unresolvable-outcome path (no matching `ContestSubmission`) not fabricating a link; regression-guard test (flagged as a residual gap in the H-20 closure audit) asserting continued absence of any automatic training-dataset write from this module's own code.

## 14. Migration Requirements

None anticipated; flag during implementation planning whether `DisputeOutcome.source_event_id` needs a uniqueness constraint for idempotency (§10) — if so, that is a small, explicitly-scoped migration proposal at that time, not written speculatively here.

## 15. Non-Goals

This module must NOT:
- Automatically retrain any model or automatically assemble a training dataset from raw outcomes (H-20, non-negotiable).
- Set `CuratedFeedbackLabel.approved_for_training` from the webhook-ingestion path.
- Modify Module F's training pipeline, `MLLabel`, or `MLDatasetMember`.
- Modify Module A's existing dispute-status webhook logic (`process_dispute_event`) unless the PO explicitly authorizes extending it (§18) rather than adding a separate endpoint.
- Modify Modules B-G.
- Modify TEST_HOLDOUT.
- Fabricate a `ContestSubmission` link for an outcome that cannot be genuinely resolved.

## 16. Dependencies

- Hard dependency: Module J. This module cannot meaningfully resolve an outcome webhook to a `ContestSubmission` until Module J has produced real ones — this is the same reasoning that kept H-19 deferred throughout this session's entire audit sequence, re-confirmed here rather than assumed.
- Depends on (frozen, unaffected): Module A's webhook-verification pattern, H-00's `DisputeOutcome`/`CuratedFeedbackLabel` schema.
- Not depended upon by: Modules I, J, L.

## 17. Acceptance Criteria

- A real outcome webhook (against a dispute actually submitted via Module J) produces exactly one `DisputeOutcome` row, correctly linked.
- `approved_for_training` is never `True` except via the explicit curation action.
- No automatic retraining or dataset-assembly code path exists anywhere in this module.
- Duplicate outcome webhooks do not produce duplicate `DisputeOutcome` rows.

## 18. Open PO Decisions

1. This entire module is gated on Module J existing and producing real submissions — confirm this dependency before any design work proceeds further than this draft.
2. Webhook design: extend Module A's existing endpoint to also carry outcome events, or add a dedicated outcome-webhook endpoint? Module A is frozen; either choice needs explicit authorization.
3. Who/what role may perform the curation action — reuse `MODEL_MAINTAINER` (currently unused by any code), define a new role, or make it a process rather than a per-row UI action?
4. Whether `DisputeOutcome.source_event_id` needs a uniqueness constraint (idempotency) — a migration question to resolve at implementation-planning time, not now.
5. Whether/when a genuinely future "Module: Retraining Pipeline" should exist at all — this draft deliberately treats it as out of scope and does not propose it, consistent with the master blueprint's own OPTIONAL classification.
