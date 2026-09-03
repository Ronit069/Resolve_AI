# Module J — Real External-Action Integration (Razorpay Client + Outbox Execution)

Status: DRAFT — for Product Owner review. Not authorized for implementation.
Continuation note: this letter continues the repository's own A-H sequence. It does **not** correspond to the master `ResolveAI_Implementation_Blueprint.md`'s own "Module J — Probability Calibration," which is already implemented inside the repository's Module F. This module instead completes the deferred external-action cluster that Module H's own audit sequence (H-09, H-11, H-13, H-14, H-15, H-16 Clause A, H-17 Clause B, H-21) identified and repeatedly declined to build ahead of its real infrastructure. See `ResolveAI_Next_Four_Modules_Research_Report.md` §2, §7 for the naming ambiguity and the PO decision this framing requires.

## 1. Objective

Build the real Razorpay HTTP client and the outbox dispatch mechanism that the six already-implemented, currently dormant Module H safety gates (H-06 deadline, H-07 amount, H-08 evidence mapping, H-10 draft/submit predicate, H-12 summary validation, plus H-18's override check already enforced in `review.py`) were built to be composed into, per H-13's own PO decision describing exactly this as future scope.

## 2. Scope

In scope: the parts of the deferred H-cluster with a **documented** external contract — contest draft/submit (H-13/H-14/H-15, unblocking H-09/H-11) and evidence document upload. Out of scope, explicitly: the dispute-accept integration (H-17 Clause B), because no accept endpoint is documented anywhere in the project's source material (§11).

## 3. Authoritative Requirements

| ID (this draft) | Requirement | Source |
|---|---|---|
| J-01 | Write approved action to DB transactionally, then dispatch asynchronously; never call external API before approval commit | E-F-G-H blueprint H-13 |
| J-02 | One approved action package has one idempotency/outbox key; retry transport failures without duplicate logical submissions | H-14 |
| J-03 | Persist every external call attempt, HTTP result, safe response metadata and retry decision | H-15 |
| J-04 | Upload required local evidence to `/v1/documents` with `purpose=dispute_evidence` and persist returned document IDs | H-09 |
| J-05 | Block submit if no valid Razorpay document ID is mapped to the contest request | H-11 |
| J-06 | On success, update local state from Razorpay response; later webhooks remain authoritative for under_review/won/lost/closed | H-16 Clause A (Clause B already satisfied — Module A, frozen, unaffected by this module) |
| J-07 | Default to simulated or Razorpay test mode; clearly distinguish DRY_RUN/DRAFT/SUBMIT in audit logs | H-21 (UI half remains Module I's/out of scope, unaffected) |
| J-08 | Documented Razorpay contract this module may implement against | E-F-G-H blueprint §6.2, §15: `PATCH /v1/disputes/:id/contest` (draft/submit actions), `POST /v1/documents` (purpose=dispute_evidence), contest amount partial/full ≤ dispute amount, contest summary ≤1000 chars, evidence attribute list (`shipping_proof`, `billing_proof`, `cancellation_proof`, `customer_communication`, `proof_of_service`, `explanation_letter`, `refund_confirmation`, `access_activity_log`, `refund_cancellation_policy`, `term_and_conditions`, others) |

## 4. Functional Requirements

- Orchestrate, in order, immediately before any external call: H-06 (`check_dispute_actionable`) → H-07 (`validate_contest_amount`) → H-08 (`evaluate_evidence_for_contest`) → H-12 (`validate_contest_summary`) → H-10 (`determine_contest_submission_action`, to decide draft vs. submit) — reusing these exact functions verbatim, not reimplementing their logic.
- Write an `ExternalActionOutbox` row transactionally with the approved `ReviewAction`/`ContestPackage`, before any HTTP call (J-01).
- Dispatch the outbox row asynchronously via a Celery task (consistent with the existing worker pattern in `app/worker/tasks.py`), calling the real Razorpay contest endpoint.
- Upload evidence documents via `POST /v1/documents` for every document the (existing, frozen) H-08 gate marks safe and mapped, persisting the returned Razorpay document ID into `RazorpayDocumentLink` (H-05 schema, frozen, currently unpopulated).
- Block submission (not draft) when H-11's condition is unmet (no mapped document ID) — reusing H-08's existing output, not a new check.
- Record every attempt (request, HTTP status, safe response metadata, retry decision) into `ExternalActionAttempt`-equivalent schema (H-07 table, frozen, currently unpopulated) — the exact table name and columns are fixed by the existing frozen migration and must be read from `app/models/module_h.py`, not redefined here.
- Default all real calls to Razorpay's test/sandbox mode per H-21's frozen requirement; never default to a live credential.

## 5. Data Model

- **Existing tables/models (frozen, H-00, currently unpopulated — this module's primary job is to start populating them correctly)**: `ExternalActionOutbox`, `ContestPackage`, `ContestPackageDocument`, `RazorpayDocumentLink`, `ContestSubmission`.
- **Required schema changes**: none anticipated. If this module's design work discovers the frozen schema is genuinely inadequate (e.g., missing a column needed to store a real Razorpay response field), that must be raised as an explicit PO decision with the specific gap named — not silently patched. This draft does not identify any such gap; the schema was designed with exactly this integration in mind.
- **New tables**: none.
- Do not invent persistence beyond what H-00 already defines.

## 6. API / Service Contracts

| Contract | Direction | Input | Output | Authorization | Validation | Error behavior | Side effects |
|---|---|---|---|---|---|---|---|
| Outbox writer (internal service call, triggered by an approved `ReviewAction`) | internal | `review_action_id`, `case_id` | `ExternalActionOutbox` row id | reuses existing H-04/H-05 gating already enforced in `review.py` at approval time — no new authorization surface | re-runs H-06/H-07/H-08/H-12/H-10 fresh (not trusting the approval-time snapshot) | any gate failure blocks the write with its existing error code; never partially writes | one transactional `ExternalActionOutbox` insert |
| `PATCH /v1/disputes/:id/contest` (external, documented) | outbound | `action` (draft/submit), `amount`, `summary`, evidence field mapping | Razorpay dispute object (`status` incl. `under_review` on submit) | Razorpay API credential (test mode default) | server-side amount/summary/evidence checks already done before this call | non-2xx recorded as a failed attempt (J-03), retried per J-02's idempotency key, never silently dropped | none locally beyond attempt/outbox status update |
| `POST /v1/documents` (external, documented) | outbound | file + `purpose=dispute_evidence` | Razorpay `doc_*` ID | same credential | H-08's existing safety/mapping check | failure recorded as attempt; submission blocked per H-11 if no ID results | writes `RazorpayDocumentLink` on success |

**Do not invent**: any endpoint, field, or response shape not listed in §15's "Current Razorpay Integration Notes" table — most importantly, no accept-path endpoint (§11).

## 7. Security / Authorization

- No new authorization surface: the outbox write happens only after `review.py`'s existing `require_role([APPROVER])` + H-05 dual-control + H-18 override checks have already finalized a `ReviewAction`. This module must re-verify H-06/H-07/H-08/H-12 freshly (never trust the approval-time snapshot — same "fresh read, not stale ORM state" discipline established by H-06 itself), but must not add a second, parallel RBAC layer.
- New for this module specifically: real outbound credential management. Razorpay API keys must come from environment/secret configuration (reusing the existing `Settings`/`.env` convention in `app/core/config.py`), never hard-coded, and must default to test/sandbox mode (H-21).

## 8. State / Workflow Rules

Reuses the existing case-state and `QueueStatus`/`ContestPackageStatus`/`OutboxStatus`/`SubmissionStatus` enums exactly as frozen in H-00 — this module is the first to actually drive `ContestPackageStatus` from `DRAFT` → `APPROVED` → `SUBMITTED`/`FAILED` and `OutboxStatus` from `PENDING` → `PROCESSING` → `SENT`/`FAILED`. No new state values are introduced.

## 9. Auditability

Every outbound attempt is recorded (J-03); every outbox transition reuses the existing `AuditLog`/audit conventions already used across Modules A-H. No parallel logging mechanism.

## 10. Idempotency / Concurrency

This is the module where H-14's idempotency-key design (already frozen in the `ExternalActionOutbox.idempotency_key` UNIQUE column) is actually exercised for the first time: one approved action package → one key → retries on transport failure must never create a second logical submission. Concurrency: reuse H-03's existing `with_for_update()` row-locking pattern for the outbox dispatch path.

## 11. External Integrations

- **Documented contracts this module may implement against** (E-F-G-H blueprint §15, verified against Razorpay's public documentation as of the source document's own stated verification date): contest draft/submit (`PATCH /v1/disputes/:id/contest`), document upload (`POST /v1/documents`).
- **Undocumented/missing contracts — must remain deferred, not inferred**: the dispute-**accept** integration (H-17 Clause B). No endpoint for this appears anywhere in the project's source material; the H-17 audit explicitly flagged this as an unresolved gap. This module must not guess a plausible-looking accept endpoint.
- **Integration boundary**: this module owns the HTTP client and outbox dispatch only; it must not duplicate or bypass the existing gate logic (H-06/07/08/10/12), which remain the single source of truth for whether an action is safe to attempt.
- **What must remain deferred**: accept-path integration (until a documented contract exists or the PO explicitly authorizes an alternative); any "won/lost" outcome-side logic (Module K's scope, not this module's).

## 12. Observability

Extends H-22's existing observability surface, once real data exists: submission success rate and API errors (the two H-22 metrics explicitly deferred pending this module, per the H-22 implementation plan's own §10) become buildable additions to the existing `app/services/observability/queue_metrics.py` pattern — additive only, not a rework of the three already-implemented H-22 metrics.

## 13. Testing Requirements

(For the eventual implementation plan.) Unit tests for the outbox writer's re-validation logic (mirroring the existing gate test style — deterministic `current_time`, `.populate_existing()` freshness checks); integration tests against a mocked/sandboxed Razorpay client (never a live call in CI); idempotency tests (duplicate dispatch attempts never produce a second `ContestSubmission`); explicit tests that a gate failure blocks the outbox write with no partial state; tests confirming test/sandbox-mode defaulting (H-21) cannot be silently overridden to a live credential.

## 14. Migration Requirements

None anticipated — H-00's schema was designed for exactly this integration and remains frozen and adequate per this draft's analysis (§5). If implementation reveals otherwise, that becomes a new, explicitly-scoped PO decision, not a speculative migration written now.

## 15. Non-Goals

This module must NOT:
- Implement the dispute-accept integration (H-17 Clause B) — no documented contract exists.
- Modify the internal logic of any of the six existing gate functions (H-06/07/08/10/12/18) — only compose/call them.
- Modify `review.py`'s existing H-03/H-04/H-05 approval logic.
- Modify H-00's frozen schema.
- Build Module K's outcome-webhook/`DisputeOutcome` writer — this module only produces the `ContestSubmission` rows K would eventually react to; it does not consume webhooks itself beyond what Module A's existing frozen ingestion already does.
- Modify Modules A-G.
- Modify TEST_HOLDOUT.
- Default to a live Razorpay credential under any circumstance (H-21).

## 16. Dependencies

- Depends on: Module H's frozen H-00 schema and six dormant gates (H-06/07/08/10/12/18); Module A's frozen webhook ingestion (unaffected, reused for later reconciliation per H-16 Clause B, already satisfied).
- Depended on by: Module K (hard dependency — see Research Report §3, §4).
- External dependency (outside all four candidate modules): real Razorpay API credentials (test/sandbox mode).

## 17. Acceptance Criteria

- A `ReviewAction` approving `APPROVE_CONTEST` results in a transactionally-written `ExternalActionOutbox` row before any HTTP call is attempted (J-01).
- Re-running the same approved action never produces a second logical Razorpay submission (J-02).
- Every attempt, success or failure, is recorded (J-03).
- A submission with no mapped, safe document ID is blocked (J-05/H-11), reusing H-08's existing output.
- All real calls default to test/sandbox mode; this cannot be overridden without an explicit, non-default configuration change (J-07/H-21).
- No accept-path code exists.

## 18. Open PO Decisions

1. Whether to build this module at all before real Razorpay sandbox credentials are provisioned (external, non-code blocker).
2. Whether to formally track this as "Module J" or as a reopened continuation of Module H's own numbering (Research Report §7, decision 2).
3. Accept-path handling: leave H-17 Clause B permanently unresolved pending a documented contract, or pursue sourcing one before any further accept-related work — no endpoint may be inferred either way.
4. Exact shape of the H-21 test/sandbox-mode config flag (naming, default value, how it's surfaced in `app/core/config.py`) — not specified by source material beyond "default to test mode."
5. Whether the outbox dispatch worker should be its own new Celery task module or extend the existing `app/worker/tasks.py`.
