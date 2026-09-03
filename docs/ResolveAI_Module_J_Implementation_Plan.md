# Module J — External Action Integration: Implementation Plan

Status: PO AUDIT / PLANNING GATE ONLY. Not authorized for implementation. No application code, migration, or endpoint was created or modified to produce this document.
Prepared against repository checkpoint `3426977` (branch `claude/resolveai-audit-h05-4tp9ns`, immediately after the Module I commit).
Continuation note: this letter continues the repository's own post-H sequence established by the PO's frozen four-module roadmap (I → Frontend/Application, **J → External Action Integration**, K → Outcome Feedback Loop, L → Deployment/MLOps/Observability). It does not correspond to the master `ResolveAI_Implementation_Blueprint.md`'s own "Module J — Probability Calibration," which is already implemented inside the repository's Module F (see `ResolveAI_Post_H_Roadmap_Reconciliation_Report.md` §2).

## 0. Objective

Determine, from repository evidence alone, exactly how much of Module J (real Razorpay contest/document submission, replacing the currently-dormant local safety gates with an actually-executed external action) can be safely planned today, and draw an explicit, evidence-grounded line between what is implementable now and what requires information this repository's source material does not contain. This document is a plan for a future PO authorization, not an authorization itself.

## 1. Exact scope (of this planning document)

Audit and plan only: (a) the outbox-writer path that composes the six existing H gates and transactionally records an approved action, and (b) the dispatch/execution path that would call the real Razorpay contest-draft/submit and document-upload endpoints, insofar as the repository's own documented Razorpay contract (E-F-G-H blueprint §15) supports it. Nothing here authorizes writing the code.

## 2. Exact non-scope

- No Razorpay HTTP call is implemented, described with invented request/response bodies, or credentialed.
- No dispute-accept integration is planned (H-17 Clause B) — repository source contains no accept endpoint contract at all (re-confirmed, §6.4 below).
- No Module K (outcome ingestion/`DisputeOutcome` writer) or Module L (deployment/MLOps) work.
- No change to Modules A-H business logic, H-00's frozen schema shape, or TEST_HOLDOUT.
- No Alembic migration.
- No modification to any of the six existing gate functions' internal logic — only composition/calling is planned.

## 3. Repository evidence

### 3.1 Current external-action architecture — FACT

- **No Razorpay HTTP client or SDK exists anywhere.** `requirements.txt` (read in full this turn) lists `httpx==0.27.0` (already a dependency, usable as a generic HTTP client — no new package would be needed for basic request/response handling) and `celery==5.3.6` / `redis==5.0.3` (the async worker infrastructure already exists and is already exercised by `app/worker/tasks.py::enrich_dispute_task`). There is no `razorpay` package, no `requests` import, no `httpx` client instantiation anywhere in `app/` outside test/library internals (re-confirmed by grep this turn).
- **Every "Razorpay" reference in the codebase is either documentation/comments, inbound webhook handling, or internal field naming** — none of them make an outbound call. Confirmed sites: `app/api/endpoints/webhooks.py` (inbound HMAC verification of `RAZORPAY_WEBHOOK_SECRET`, Module A, frozen), `app/schemas/module_a.py` (inbound payload schemas), `app/services/external_action/evidence_mapping_gate.py` (a `Dict[EvidenceType, str]` mapping table to Razorpay's documented evidence field names — a pure lookup table, no HTTP), `app/models/module_h.py` (column/table names only).
- **No credential configuration exists.** `app/core/config.py`'s `Settings` class (read in full this turn) has exactly one Razorpay-related field, `RAZORPAY_WEBHOOK_SECRET` (inbound signature verification only). There is no API key, secret, base URL, or mode (test/live) field of any kind.
- **Retry/backoff convention already exists and is reusable**: `enrich_dispute_task` (`app/worker/tasks.py`) demonstrates the established pattern — `@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)`, exponential backoff via `self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))` for a named transient-error exception class, and unconditional `ProcessingError` recording for terminal failures. Any Module J dispatch task should reuse this exact shape, not invent a new one.
- **Idempotency convention already exists and is reusable**: Module A's `WebhookEvent.external_event_id` UNIQUE constraint (inbound dedup) and H-00's `ExternalActionOutbox.idempotency_key` UNIQUE constraint (outbound dedup, already frozen schema) are the two established idempotency mechanisms in this codebase. Module J reuses the second, not a new one.

### 3.2 Persistence lineage — FACT, full schema re-read this turn

All from `app/models/module_h.py`, frozen since the H-00 migration, currently zero rows in every table below (re-confirmed):

| Table | Key columns | Relationships |
|---|---|---|
| `ContestPackage` (H3) | `id, case_id→cases, review_action_id→review_actions, draft_id→generated_drafts, contest_amount_minor, summary, package_hash, status (DRAFT/APPROVED/SUBMITTED/FAILED), created_at` | The frozen point of assembly: one row per approved `ReviewAction` |
| `ContestPackageDocument` (H4) | `id, contest_package_id→contest_packages, document_id→evidence_documents, razorpay_evidence_field, approved, sort_order` | Links approved evidence documents to their mapped Razorpay field (H-08's output) |
| `RazorpayDocumentLink` (H5) | `id, document_id→evidence_documents, razorpay_document_id (UNIQUE), purpose, mime_type, size_bytes, uploaded_at, external_response_json` | Would record the real `doc_*` ID Razorpay's `POST /v1/documents` returns |
| `ExternalActionOutbox` (H6) | `id, case_id→cases, action_type (UPLOAD_DOCUMENT/CONTEST_DRAFT/CONTEST_SUBMIT/ACCEPT), aggregate_id (UUID, no FK constraint), payload_json, idempotency_key (UNIQUE), status (PENDING/PROCESSING/SENT/FAILED), attempt_count, next_attempt_at, created_at` | The transactional write-before-dispatch record (H-13) |
| `ExternalActionAttempt` (H7) | `id, outbox_id→external_action_outbox, attempt_no, request_metadata, http_status, response_metadata, error_code, started_at, completed_at` | Per-attempt history (H-15) |
| `ContestSubmission` (H8) | `id, contest_package_id→contest_packages, external_dispute_id, action (string, "draft"/"submit"), external_status, submitted_at, razorpay_evidence_json, response_snapshot, status (SUCCESS/FAILED/PENDING)` | The record of what was actually sent/returned |
| `DisputeOutcome` (H9) | out of scope — Module K | — |

`aggregate_id` on `ExternalActionOutbox` has **no FK constraint** (confirmed by reading the column definition — `Column(UUID(as_uuid=True), nullable=False)`, no `ForeignKey(...)`) — this is deliberate polymorphism (it can point at a `ContestPackage.id` for CONTEST_DRAFT/CONTEST_SUBMIT or an `EvidenceDocument.document_id` for UPLOAD_DOCUMENT), not an oversight; Module J must not "fix" this by adding a constraint.

### 3.3 Existing H contracts Module J must consume — FACT, exact signatures re-read this turn

All six are pure, dormant, already-tested functions in `app/services/external_action/` and `app/services/review/dual_control.py`. None accept caller-supplied authoritative state; all re-read canonical rows fresh (`.populate_existing()` pattern). Exact signatures:

- `check_dispute_actionable(db: Session, case_id: UUID, current_time: Optional[datetime] = None) -> DeadlineGateResult` (H-06)
- `validate_contest_amount(db: Session, case_id: UUID, contest_amount_minor: int, current_time: Optional[datetime] = None) -> ContestAmountGateResult` (H-07)
- `is_document_safe_for_contest(db: Session, case_id: UUID, document_id: UUID, current_time: Optional[datetime] = None) -> DocumentSafetyResult` and `evaluate_evidence_for_contest(db, case_id, document_id, evidence_type, current_time=None) -> EvidenceEligibilityResult` (H-08)
- `determine_contest_submission_action(db: Session, case_id: UUID, current_time: Optional[datetime] = None) -> ContestSubmissionActionResult` — returns `action: "submit" | "draft"` based on whether the case's most recent `ReviewQueueItem` is `DONE` with a finalized `APPROVE_CONTEST` (H-10)
- `validate_contest_summary(db: Session, case_id: UUID, candidate_summary: Optional[str], current_time: Optional[datetime] = None) -> ContestSummaryGateResult` (H-12)
- `requires_dual_approval(action: ReviewActionEnum, prediction: RiskPrediction, dispute: Dispute) -> bool` (H-05, in `app/services/review/dual_control.py`) — already enforced at approval time in `review.py`; Module J does not re-implement dual control, only relies on the fact that a `ReviewAction` reaching `DONE` via `APPROVE_CONTEST` already passed it.
- H-18's override-justification check (`_needs_h18_override` in `review.py`) is likewise already enforced at approval time; Module J does not re-check it.

**Design implication, not yet code**: an outbox-writer function would call H-06 → H-07 → H-08 (per document) → H-12 → H-10 in that order, immediately before writing `ExternalActionOutbox`, re-validating fresh rather than trusting the `ReviewAction`/`ContestPackage` snapshot from approval time — mirroring exactly the "fresh read, not stale state" discipline each gate's own docstring states it exists for.

### 3.4 Existing API contracts — FACT

- `POST /api/v1/cases/{case_id}/review-action` (`review.py`, H-03/04/05/18, frozen) is the only mutation that currently exists in the review lifecycle; it creates a `ReviewAction` and, on `APPROVE_CONTEST`/`APPROVE_ACCEPT` without pending dual control, sets `ReviewQueueItem.queue_status = DONE`. It does **not** call any gate in §3.3, does **not** write `ContestPackage`, and does **not** perform any external action — confirmed by full re-read in this session's Module H closure audit and unchanged since (`git log` shows no commits touching `review.py` since `a98b850`).
- **The precise missing contract**: there is no endpoint, service function, or task anywhere that takes a `DONE`+`APPROVE_CONTEST` `ReviewAction` and produces a `ContestPackage` row, nor one that takes a `ContestPackage` and dispatches it externally. This is the entire gap Module J exists to fill — confirmed empty, not assumed.
- No accept-path endpoint of any kind exists (H-17 Clause B) — re-confirmed by grep (`grep -rn "accept" app/api/endpoints/`, no Razorpay-accept-shaped route found).

### 3.5 Current Razorpay contract as documented in repository source — FACT, this is the entire available contract, quoted verbatim

`docs/ResolveAI_Modules_E_F_G_H_Technical_Blueprint.md` §6.2 and §15 (the only source material describing Razorpay's real API anywhere in this repository) state:

> Current official Razorpay documentation exposes a dispute contest endpoint `PATCH /v1/disputes/:id/contest`. Evidence documents can be uploaded via `POST /v1/documents` with `purpose=dispute_evidence`. The contest endpoint supports draft and submit actions; submission requires at least one document ID, and successful submission moves an open dispute to `under_review`. Evidence attributes include `shipping_proof, billing_proof, cancellation_proof, customer_communication, proof_of_service, explanation_letter, refund_confirmation, access_activity_log, refund_cancellation_policy, term_and_conditions` and others.

§15's table adds: contest amount can be partial/full, cannot exceed dispute amount (already enforced locally by H-07); contest summary maximum length 1000 characters (already enforced locally by H-12, `SUMMARY_MAX_LENGTH` config); document upload returns a `doc_*` ID used in dispute evidence; successful submission triggers a `payment.dispute.under_review` webhook; outcome webhooks include `created, action_required, under_review, won, lost, closed`.

**This is the entire documented contract.** Nothing else about Razorpay's real API appears anywhere in this repository's source material.

## 4. Current architecture summary

```
[approved ReviewAction, DONE, APPROVE_CONTEST]  <-- exists today, review.py, frozen
        |
        v  <-- MISSING: no code builds a ContestPackage from this
[ContestPackage assembly]  <-- H3/H4 schema exists, zero writer code
        |
        v  <-- MISSING: no code composes the six gates + writes the outbox
[ExternalActionOutbox write, transactional]  <-- H6 schema exists, zero writer code
        |
        v  <-- MISSING: no dispatch worker, no HTTP client
[Celery dispatch task -> Razorpay HTTP call]  <-- does not exist; contract only
        |                                          partially documented (§3.5)
        v
[ExternalActionAttempt + ContestSubmission write]  <-- H7/H8 schema exists, zero writer code
```

## 5. Dependency / prerequisite matrix

Per the task's required A/B/C/D split:

**A. Already available prerequisites**
- Six dormant, tested gate functions (§3.3), callable as-is.
- Frozen, correctly-shaped H3–H8 persistence schema (§3.2), including the outbox's idempotency-key UNIQUE constraint.
- Reusable Celery retry/backoff pattern (`enrich_dispute_task`).
- `httpx` already a project dependency (no new package needed for a generic HTTP call).
- `app/core/config.py`'s existing `Settings`/`.env` convention for adding credential fields when authorized.
- The `ReviewAction`/`ReviewQueueItem` DONE+APPROVE_CONTEST state as the trigger condition (already reliably producible today).

**B. Prerequisites that can be implemented within Module J** (given a PO decision to proceed, but requiring no new information beyond what's already documented)
- The `ContestPackage`/`ContestPackageDocument` assembly function (pure local logic: read the approved `ReviewAction`, its `GeneratedDraft`, and its evidence documents; call H-08 per document; compute `package_hash`; write `ContestPackage` + `ContestPackageDocument` rows).
- The outbox-writer function that re-runs H-06/H-07/H-08/H-12/H-10 fresh and, only if all pass, transactionally writes one `ExternalActionOutbox` row with a deterministic `idempotency_key` (e.g. derived from `contest_package_id` + `action_type`) — this requires no Razorpay contract knowledge at all, only the existing gates and schema.
- The Celery dispatch task's *retry/idempotency/attempt-recording scaffolding* (claiming a `PENDING` outbox row, incrementing `attempt_count`, writing an `ExternalActionAttempt` row, backoff on transient failure) — the *shape* of this is fully specifiable from §3.1's existing pattern without knowing Razorpay's exact request/response format.
- The document-upload half's local bookkeeping (which documents are eligible per H-08, in what order) — again, everything except the actual HTTP call.

**C. Prerequisites that require a separate PO decision** (not blocked by missing information, but by scope/business choices)
- Whether to build this at all before real Razorpay credentials exist (a sequencing choice, not a technical blocker).
- The exact `idempotency_key` derivation formula (any deterministic, collision-free scheme works; which one is a convention choice).
- Test-mode-vs-live credential selection mechanism/config field naming (H-21's own frozen requirement to default to test mode; the concrete config shape is undecided).
- Whether `ExternalActionAttempt.request_metadata`/`response_metadata` should redact anything before persistence (a data-handling policy choice, not a missing fact).

**D. Information/API contracts genuinely unavailable — must not be invented** (this is the hard boundary the task requires stopping at)
- **Authentication scheme for outbound Razorpay calls.** Nothing in repository source states how a request would be credentialed (API key header format, HTTP Basic Auth, bearer token, or otherwise). `RAZORPAY_WEBHOOK_SECRET` is inbound-only and does not describe outbound auth. **Not invented here.**
- **Full request/response JSON schemas** for `PATCH /v1/disputes/:id/contest` and `POST /v1/documents`. §3.5 documents the endpoint's existence, its two actions, the evidence-field vocabulary, and two behavioral facts (amount ceiling, summary length) — it does not document the full request body shape, the full response body shape, or field names beyond the evidence-attribute list. **Not invented here.**
- **Base URL / test-vs-live selection mechanism.** No sandbox or test-mode base URL, header, or account-level flag is documented anywhere in repository source. H-21 requires defaulting to test mode but does not specify how that is technically expressed against Razorpay's real API. **Not invented here.**
- **Error response format.** No documented error schema, error codes, or HTTP status conventions beyond generic REST assumptions. **Not invented here.**
- **Idempotency behavior of the real endpoint.** Whether Razorpay's contest-submit endpoint is itself idempotent on retry (e.g., accepts a client-supplied idempotency key, or naturally dedupes by dispute ID + action) is not documented. This directly affects §7 (Idempotency and failure semantics) below — the safe design assumes the answer is unknown, not that it is idempotent.
- **The dispute-accept endpoint entirely** (H-17 Clause B) — no route, method, or behavior is documented anywhere. Re-confirmed, not new.
- **Rate limits / retry-after conventions.**

**Everything in category D blocks writing the actual outbound HTTP call and parsing its response.** Categories A and B do not.

## 6. Proposed file-level changes (planning only — not created)

| Path | Action | Buildable now? |
|---|---|---|
| `app/services/external_action/contest_package_assembly.py` | New | **Yes (Category B)** — pure local read/compose/write, no Razorpay contract needed |
| `app/services/external_action/outbox_writer.py` | New | **Yes (Category B)** — composes the six existing gates fresh, writes `ExternalActionOutbox` transactionally, no HTTP call |
| `app/worker/external_action_tasks.py` | New | **Partially** — the Celery task shell, claim/retry/attempt-recording scaffolding is Category B; the actual `httpx` call to Razorpay inside it is Category D and cannot be written |
| `app/services/external_action/razorpay_client.py` | New | **No — blocked (Category D)**. Cannot be written without the auth scheme, request/response schema, and base URL/mode selection. This file would need to exist for Module J to actually reach Razorpay, and this plan explicitly stops here rather than stub it with invented behavior. |
| `app/core/config.py` | Modify (additive) | Credential/mode fields — **blocked until Category C/D are resolved** (field names depend on the unresolved auth scheme) |
| `alembic/versions/` | None planned | H3–H8 schema is already adequate (re-confirmed §3.2); no migration identified as necessary |

No endpoint file is proposed — Module J's trigger is the existing `DONE`+`APPROVE_CONTEST` `ReviewAction` state, observed by a worker/service, not a new HTTP-facing route in this plan. (If a manual "retry submission" admin action were ever wanted, that would need its own PO decision — not assumed here.)

## 7. Proposed API contracts

**Internal (buildable, Category B)**: none of Module J's internal-facing pieces need a new HTTP endpoint per §6 — they are service functions and a Celery task, triggered by state, not by a request.

**External (Razorpay-facing)**: **none can be specified.** Per §5 Category D, the request method/path/action values are documented (`PATCH /v1/disputes/:id/contest`, `POST /v1/documents`), but the request body shape, headers (including auth), and response body shape are not. Specifying a concrete contract here would require inventing fields not present in repository source, which this plan explicitly declines to do.

## 8. External API boundary — explicit unknowns

Restating §5 Category D as the single authoritative unknowns list for this plan:
1. Outbound authentication mechanism — **unknown**.
2. Full request body schema for both endpoints — **partially known** (evidence field vocabulary, action values, amount/summary constraints; not known: complete field list, nesting, required vs. optional fields).
3. Full response body schema for both endpoints — **partially known** (document upload returns a `doc_*` ID; contest submit moves status to `under_review`; not known: complete response shape).
4. Base URL and test/live mode selection — **unknown**.
5. Error response format — **unknown**.
6. Retry-safety / idempotency behavior of the real endpoint — **unknown**.
7. The accept-dispute endpoint (method, path, everything) — **entirely unknown**, not merely incomplete.

## 9. Persistence changes

**None justified.** §3.2's schema re-read confirms H3–H8 already carry every column the Category B pieces (§6) would need to populate (`ContestPackage.package_hash`, `ExternalActionOutbox.idempotency_key`, `ExternalActionAttempt.request_metadata`/`response_metadata`, `ContestSubmission.response_snapshot`). If Category D is eventually resolved and a real response shape is known, it is expected to fit into the existing `JSONVariant` (`payload_json`, `*_metadata`, `*_json`) columns without a schema change — but this is a forward expectation, not a proposal to migrate now.

## 10. Idempotency design

Buildable now (Category B), reusing existing mechanisms only:
- One `ContestPackage` per approved `ReviewAction` (natural 1:1 via `review_action_id`, currently unique in intent though not DB-constrained — **flag**: `ContestPackage.review_action_id` has an FK but no UNIQUE constraint in the current schema; the outbox-writer function would need to check-then-write or rely on the outbox's own idempotency key rather than assume the DB enforces this. This is an observation about existing schema, not a proposed migration.).
- One `ExternalActionOutbox` row per `(contest_package_id, action_type)` pair, enforced via a deterministic `idempotency_key` construction (e.g. a hash of `contest_package_id + action_type`) feeding the already-existing UNIQUE constraint — a duplicate write attempt fails at the DB level, not via new application logic.
- Retry of a `PENDING`/`FAILED` outbox row is safe to attempt again **at the local level** (no new external effect has necessarily happened) — but see §11 for why this is not fully safe once a real HTTP call is involved.

**Not buildable (Category D)**: whether *retrying the actual Razorpay call* risks a duplicate external submission depends entirely on the unknown idempotency behavior of Razorpay's real endpoint (§8.6). This plan cannot specify a safe retry policy for the HTTP call itself.

## 11. Failure semantics

- **Local failures** (gate re-validation fails, DB write fails): fully specifiable now — the outbox write simply doesn't happen; `ContestPackageStatus` stays `DRAFT`/never reaches `APPROVED`; nothing external is attempted. Safe, Category B.
- **Transient transport failures** (connection refused, 5xx, timeout with no response received): the existing Celery retry pattern (§3.1) is reusable for the *scaffolding* — record the attempt, backoff, retry up to a bounded count. But **whether it is safe to retry a call that may have already reached Razorpay's server** depends on the unknown idempotency behavior (§8.6) — this plan flags this as a real risk it cannot resolve, not a solved problem.
- **Timeout / unknown result** (request sent, no response received before timeout): the only safe default this plan can specify without inventing external behavior is: mark the attempt `FAILED` with `error_code="UNKNOWN_RESULT"`, do **not** automatically retry, and require either (a) a manual reconciliation step, or (b) tying into H-16 Clause A's future webhook-reconciliation design (already noted in the Module J blueprint as deferred) to authoritatively resolve state from Razorpay's own webhook rather than guessing. Blind auto-retry on an unknown-result timeout is explicitly **not** recommended given the unresolved idempotency question.
- **Permanent failures** (validation rejection from Razorpay, once its error format is known): would set `ContestSubmission.status = FAILED` and `ContestPackageStatus.FAILED` — mechanically specifiable now, though the *classification* of which errors are permanent vs. transient depends on the unknown error format (§8.5).

## 12. Authorization / security

- No new authorization surface needed for the Category B pieces — they trigger off the already-authorized `DONE`+`APPROVE_CONTEST` state, which only exists because `review.py`'s existing `require_role([APPROVER])` + H-05 + H-18 checks already passed at approval time. Module J does not add a second human-facing authorization gate; it re-validates *data freshness* (the six gates), not *permission*.
- **Tenant isolation**: every gate in §3.3 already takes `case_id` and internally scopes correctly (re-confirmed across this session's H-item audits); the outbox-writer and dispatch task must scope by `case_id`/`merchant_id` the same way — no new isolation mechanism needed.
- **Credential handling**: per §5 Category C/D, once an auth scheme is known, credentials must go through the existing `Settings`/`.env` convention (`app/core/config.py`), never hard-coded, never logged. This plan does not propose credential handling because the scheme is unknown (§8.1).
- **No production credential assumption**: per instruction, this plan assumes test/sandbox-only credentials would ever be configured, consistent with H-21's frozen "default to test mode" requirement — but see §8.4 for why even that cannot be technically specified yet.
- **Correlation/request IDs**: not currently used anywhere in this codebase (confirmed absent in the Module I audit's repository findings); Module J would be the first place they'd matter (correlating an `ExternalActionAttempt` with its outbound request) — a **RECOMMENDATION**, not yet a repository convention to reuse, so this plan flags it as worth adding rather than assumes it exists.

## 13. Audit requirements

`ExternalActionAttempt` (H7) already exists specifically to record every attempt with request/response metadata (§3.2) — reusing this, not inventing a new audit table, satisfies the audit requirement for whatever Category B scaffolding is built. No new audit mechanism is proposed.

## 14. Test matrix

Buildable now (Category B, no live Razorpay call involved):
- **Unit**: `contest_package_assembly.py` — correct `ContestPackage`/`ContestPackageDocument` construction from a known `ReviewAction`+`GeneratedDraft`+evidence set; `outbox_writer.py` — re-validates fresh (mutate underlying rows via a separate session, assert the writer sees the fresh value, mirroring the existing gate-test pattern); blocks the write when any of the six gates fails; idempotency-key collision is rejected at the DB level (duplicate write attempt raises the existing UNIQUE constraint, not a new check).
- **Integration**: outbox-writer called against a fully-seeded case (mirroring `test_module_h_step*.py`'s fixture conventions) produces exactly one outbox row; a case failing H-07 (amount) or H-12 (summary) produces zero rows.
- **Mocked external boundary**: the Celery dispatch task's retry/backoff/attempt-recording logic tested against a **mocked** HTTP layer (e.g., a fake client raising a connection error, a fake 5xx, a fake timeout) — testing the *scaffolding*, not real Razorpay behavior, since the real contract is unknown.
- **Idempotency/retry**: duplicate dispatch attempts on the same outbox row never produce a second `ContestSubmission`; retry count is bounded and observable via `ExternalActionAttempt` rows.
- **Failure/timeout**: the "unknown result" path (§11) sets the documented safe state and does not auto-retry.

**Not buildable**: any test asserting a specific real Razorpay request/response shape, since that shape is unknown (§8). **No live Razorpay call in any test, under any circumstance, unless the PO explicitly provides an authorized sandbox environment** — none exists today (confirmed: no test file anywhere references a live external call, and `ENABLE_DEV_ENDPOINTS`-style flags govern only internal dev/synthetic endpoints, not outbound calls).

## 15. Migration impact

None. §3.2/§9 confirm the existing H3–H8 schema is adequate for everything in Category B; nothing in this plan requires a new column, table, or index.

## 16. Rollback considerations

- Category B pieces (outbox writer, package assembly, dispatch scaffolding) are purely additive new files plus, at most, one new Celery task registration — rollback is a straightforward revert of those files, identical in kind to every prior module's rollback profile (delete the new files, remove the task registration).
- No migration means no schema rollback risk.
- Because no Category D code would exist yet, there is no outstanding external side effect to roll back — the moment this plan's Category B scope stops (at the boundary of the actual HTTP call), nothing has touched Razorpay, so there is nothing external to reconcile if the work is reverted.

## 17. Implementation order (for a future authorization, not proposed for now)

1. `contest_package_assembly.py` (Category B, no dependency on anything else in this list).
2. `outbox_writer.py` (Category B, depends on #1 producing a `ContestPackage`).
3. Celery dispatch task scaffolding only — claim/retry/attempt-recording, with the actual HTTP call left as an explicitly unimplemented boundary (Category B up to that line).
4. **Hard stop** pending PO resolution of §5 Category D (real Razorpay contract details) before the HTTP call itself can be written.
5. Once Category D is resolved: `razorpay_client.py`, config fields, and the completion of the dispatch task.

## 18. Explicit PO decisions required

1. Whether to authorize building Category B now (outbox writer + package assembly + dispatch scaffolding) even though the actual Razorpay call cannot yet be written — i.e., build up to the documented boundary and stop there, or wait until Category D is fully resolved before starting anything.
2. How Category D's unknowns get resolved: does the PO have access to Razorpay's real API documentation/credentials beyond what's already quoted in §3.5, or does someone need to source it before Module J can proceed past step 3 of §17?
3. `idempotency_key` derivation convention (§10) — any deterministic scheme works; which one is a naming/convention choice.
4. Correlation/request ID convention (§12) — recommended as new infrastructure, not yet decided.
5. Credential/test-mode config field shape — blocked on #2, but the naming convention itself (once unblocked) is a small separate choice.
6. Whether H-17 Clause B (accept path) should remain permanently out of scope for Module J given zero documented contract exists, or whether sourcing that contract is a parallel, separate effort.

## 19. Explicit blockers

- **Hard blocker**: the real Razorpay outbound contract (authentication, full request/response schemas, base URL/mode selection, error format, retry-safety) is not documented anywhere in this repository's source material (§5 Category D, §8). This blocks `razorpay_client.py`, the completion of the dispatch task, any config credential fields, and any accept-path work entirely. This plan does not and will not fabricate these to unblock itself.
- **Soft blocker**: no live or sandboxed Razorpay test environment is referenced anywhere in the repository (§14) — even once Category D is resolved on paper, testing against a real endpoint would need an explicitly PO-authorized environment, not assumed to exist.
- **Not a blocker**: Category B (outbox writer, package assembly, dispatch scaffolding) has zero missing information and could be planned into a concrete implementation plan today, pending only a PO decision to proceed (§18.1).

## 20. Acceptance criteria (for whatever scope the PO eventually authorizes)

- If Category B alone is authorized: an approved `ReviewAction` (`DONE`+`APPROVE_CONTEST`) reliably produces exactly one `ContestPackage` and, only if all six gates pass on a fresh re-check, exactly one `ExternalActionOutbox` row with a collision-safe idempotency key — with zero outbound HTTP calls made anywhere in this scope, verified by the same "no external call capability in module" test pattern already used for H-06/H-08's dormant gates.
- Category D-dependent work has no acceptance criteria in this plan — it cannot be defined until the unknowns in §8 are resolved by the PO.

---

## 21. Category B implementation notes (added after Category B authorization and build)

Recorded facts discovered while implementing the authorized Category B scope — not a roadmap change:

- **Idempotency keys are stored as SHA-256 hashes** of the logical identity strings this plan specifies (`contest_package:{id}:upload:{document_id}`, `contest_package:{id}:contest:{draft|submit}`), not as the raw strings themselves. This is purely a storage-safety implementation choice (guarantees a fixed, safely-bounded length against `idempotency_key`'s `String(255)` column regardless of future ID formats) — the logical identity and its determinism are unchanged from §10/the PO's authorization; two calls with the same logical inputs always hash to the same key.
- **H-05 (`requires_dual_approval`) is composed as an informational consistency field only**, not a pass/fail gate: the outbox-writer calls it fresh and records its boolean result in the gate-check summary for audit purposes, but does not block on it — re-deriving it as a blocking condition would duplicate the approval-time enforcement `review.py` already performed (per this authorization's explicit "do not duplicate their business logic" instruction). The actual safety property is enforced transitively: H-10's fresh `DONE`+`APPROVE_CONTEST` requirement cannot be satisfied while dual control is still pending.
- **`ContestPackage` assembly fails closed when `GeneratedDraft.contest_amount_minor` is `None`** (a real, nullable case in the frozen schema, documented as "may be None for REVIEW/ACCEPT") — no package is assembled and no amount is inferred or defaulted.
- **`ContestPackageDocument` fresh-recheck at outbox-write time is fail-closed as a whole**: if any document already approved into the package at assembly time is no longer safe/mapped on the fresh H-08 re-check, the entire outbox write is blocked (no partial document set is dispatched) rather than silently dropping just that document.

---

## Repository verification (this planning turn)

- `git rev-parse HEAD`: `3426977c658054155b181860bc927eb0bda74a03` (unchanged throughout this audit).
- `git status --short`: only this new file is untracked; zero application-code changes.
- Files created: `docs/ResolveAI_Module_J_Implementation_Plan.md` (this document) only.
- Files modified: none.
- Zero application-code, migration, test, or config changes were made to produce this plan — all findings above were gathered via `Read`/`Bash grep`/`git log` inspection only.

(§21 above was added in a subsequent, PO-authorized Category B implementation turn — see that turn's completion report for full build/test/regression results. HEAD at that point remained unmoved from `3426977` until this document's own update, since nothing was committed.)
