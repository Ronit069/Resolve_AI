# Post-H Roadmap Reconciliation Report

Status: RESEARCH ONLY — for Product Owner review. No module letters assigned. No implementation authorized. This document supersedes nothing; it reconciles and narrows the prior `ResolveAI_Next_Four_Modules_Research_Report.md` and its four draft blueprints, which remain untouched and unapproved pending this reconciliation.

Prepared against repository checkpoint `23160a1` (branch `claude/resolveai-audit-h05-4tp9ns`), immediately following the Module H MVP closure audit and the (not-yet-approved) four-module draft set.

## 0. Classification method

Every finding below is tagged with exactly one of six labels, per the task's explicit instruction:

- **SOURCE-EXPLICIT** — a requirement stated directly in a blueprint document, quoted or closely paraphrased with a citation. Not a judgment call.
- **ALREADY IMPLEMENTED** — repository-verified: real, tested, currently-exercised code satisfies the requirement.
- **DORMANT** — code exists (schema and/or pure functions) that correctly implements part of the requirement, but nothing in the live system calls or populates it yet.
- **DEFERRED** — a PO decision (in this session's prior H-item audits) explicitly declined to build this, naming a specific blocker.
- **MISSING** — no code, schema, or explicit deferral exists; genuinely unaddressed.
- **YOUR RECOMMENDATION** — this report's own reasoned proposal. Never presented as fact; always separated from the five labels above.

Evidence for A-H claims reuses this session's own H-00→H-22 closure audit (source-grounded, re-verifiable against `git log`/`grep`/`Read`, not re-litigated here) plus fresh verification this turn of Modules B, C, D, G's actual schema/service code (not audited item-by-item earlier in this session, since only H was in scope until now).

---

## 1. Requirements from the master blueprint that remain genuinely unowned/unimplemented after A-H

**SOURCE-EXPLICIT** (master blueprint, `ResolveAI_Implementation_Blueprint.md`), cross-checked against the repository this turn:

| Requirement | Source | Repo status |
|---|---|---|
| Frontend (§9: 7 screens, all roles) | §9, §2 | **MISSING** — zero `frontend/`/`web/`/`apps/` directory anywhere |
| Real authentication (JWT/session, §12) | §12 "Authentication" | **MISSING** — `app/api/deps.py:10` contains the literal comment "In a real app, this would verify a JWT"; actual auth is a bare `X-User-Id` header, confirmed this turn by direct read |
| Rate limiting / request-size limits (A-11, "Should") | A-B-C doc §3.3 | **MISSING** — zero references to rate limiting anywhere in `app/` (confirmed by grep this turn: no `slowapi`, no rate-limit middleware) |
| Correlation/request IDs across API/worker/audit (SH-08, "Should"; master §7.1) | A-B-C doc §2, master §7.1 | **MISSING** — confirmed by grep this turn: no `correlation_id`/`request_id`/`X-Request-Id` field or header anywhere in `app/` |
| Experiment tracking / model registry UI (MLflow or equivalent) | master §13 | **MISSING** — zero MLflow references anywhere; `ModelVersion` table exists but no artifact/registry tooling around it |
| Runtime observability (inference/OCR/LLM latency, queue duration, error rate) | master §13 | **MISSING** — distinct from H-22's review-queue metrics (queue age/near-deadline/turnaround), which are implemented; this is a different, entirely unbuilt metric family |
| Full Docker deployment (api/worker/web services, one-command startup) | master §20, §20.1, §22 | **MISSING** — `docker-compose.yaml` defines only `postgres`/`redis`/`minio`; no `Dockerfile` anywhere in the repository |
| README / architecture documentation | master §22 | **MISSING** — `README.md` is a zero-byte file (confirmed this turn) |
| Real external Razorpay contest/document integration | master §5.14 row "Approve contest", E-F-G-H blueprint H-09/H-13/H-14/H-15 | **MISSING (code) / DEFERRED (decision)** — see §3 and §4 below; distinguished from "missing" because this one has an explicit PO deferral record, unlike the items above which were simply never addressed |
| Razorpay dispute-**accept** integration | master §5.14 row "Accept dispute"; E-F-G-H blueprint H-17 Clause B | **MISSING, and the underlying contract itself is undocumented** — confirmed in this session's H-17 audit: no accept endpoint appears anywhere in the project's source material. This is the one item in this whole report where the gap is not just "unbuilt" but "unspecifiable without new source material" |
| Outcome webhook ingestion + `DisputeOutcome` population | master §5.15 Module O | **MISSING (code) / DEFERRED (decision)** — H-19; schema-frozen, zero writer code, explicit PO deferral on record |
| Drift monitoring / automated retraining | master §13 "Quality monitoring... once outcomes exist"; E-F-G-H blueprint §12.1 marks this OPTIONAL | **MISSING**, and explicitly marked non-required by source (§12.1: "Automated retraining/drift alert UI... keep design-ready if time is limited") |

## 2. Master-blueprint requirements already satisfied inside A-H

**ALREADY IMPLEMENTED** (repository-verified, most re-confirmed this session's H-00→H-22 audit; Modules B/C/D/G re-verified fresh this turn):

- **Module A — Secure Dispute Ingestion**: webhook signature verification, idempotency via `WebhookEvent.external_event_id` UNIQUE constraint, staleness-protected state transitions, `DisputeEvent` audit trail — `app/api/endpoints/webhooks.py`, `app/services/ingestion.py`. Matches A-B-C doc §3.3 A-01 through A-10 (A-11 rate-limiting and A-12 dead-letter routing are the two "Should" items not built — see §1).
- **Module B — Transaction and Order Enrichment**: fully modeled this turn — `Payment`, `Order`, `Shipment`, `Refund`, `CustomerHistory`, `CaseEnrichment` tables (`app/models/module_b.py`), each with correct tenant-scoped uniqueness constraints (`case_id` + external ID, not globally unique — matches A-B-C doc's own stated design rationale), populated by `app/services/enrichment.py` and `app/api/endpoints/enrichment.py`, with a dedicated `test_module_b.py` test file. Matches master §5.2 and A-B-C doc §4 closely.
- **Module C — Evidence Intake and File Security**: `EvidenceDocument`, `MalwareScanResult`, `EvidenceRequirement`, `CaseEvidenceStatus`, `EvidenceAccessEvent` (`app/models/module_c.py`); a real (deterministic, hash-based) scanner service (`app/services/scanner.py`) that quarantines/rejects based on scan outcome — satisfies master's "at minimum reject executable/polyglot formats for MVP" fallback language exactly, not a stub with no behavior.
- **Module D — Document Intelligence Pipeline**: `DocumentProcessingJob`, `DocumentPage`, `DocumentExtraction`, `ExtractedField`, `DocumentQualityAssessment`, `DocumentModelVersion` (`app/models/module_d.py`) — matches master §5.4's OCR/extraction/normalization/confidence/provenance pipeline.
- **Module E (repo's own definition) — Evidence Validation and Feature Preparation**: matches master's F (Cross-Document Consistency) + G (Feature Engineering) combined — already established in this session's prior research report.
- **Module F — Cost-Sensitive Risk ML and Decision Recommendation**: matches master's H + I + J + K + L combined (ML training/model selection, cost-sensitive evaluation, calibration, three-way policy decision, SHAP explainability) — already established in this session's prior research report, re-confirmed here.
- **Module G — Policy-Grounded RAG and LLM Auto-Responder**: matches master's E (RAG Knowledge Base) + M (Grounded LLM Response Generation) combined. Fresh confirmation this turn: `KnowledgeSource`, `KnowledgeChunk`, `RagRetrievalRun`, `RagRetrievedChunk` (RAG/E-equivalent) and `ResponseGenerationRun`, `GeneratedDraft`, `DraftClaim`, `LLMGuardrailResult`, `RagEvaluationQuery`, `RagEvaluationRun` (LLM generation/M-equivalent) all exist in `app/models/module_g.py`.
- **Module H — Human Review, Approval, RBAC, Dual Control, Override Justification**: matches master's N (Human-in-the-Loop Approval) — closed and READY per the prior closure audit.
- **Module H's local safety-gate half of the external-action cluster** (H-06 deadline recheck, H-07 amount validation, H-08 evidence mapping, H-10 draft/submit predicate, H-12 summary validation): all implemented and tested, though DORMANT in the sense defined below (§0).
- **Dead-letter-equivalent error handling** (A-12, "Should"): the `ProcessingError`/`processing_errors` table (`app/models/shared.py`) is actively used across `app/services/evidence.py`, `app/services/enrichment.py`, and `app/worker/tasks.py` (confirmed by grep this turn) — this satisfies A-12's intent ("Failed events are observable and can be reprocessed safely") even though it is not a literal message-queue dead-letter mechanism.

## 3. Requirements that are partially implemented / dormant / deferred

**DORMANT** (code exists, correctly built, currently unreachable from any live code path):
- H-06 (`deadline_gate.py`), H-07 (`contest_amount_gate.py`), H-08 (`evidence_mapping_gate.py`), H-10 (`contest_submission_action_gate.py`), H-12 (`contest_summary_gate.py`) — all confirmed this session (H-cluster closure audit) to be tested, correct, and un-called by any endpoint or worker task, because the orchestrating consumer (a real Razorpay client) does not exist.
- `ExternalActionOutbox`, `ContestPackage`, `ContestPackageDocument`, `RazorpayDocumentLink`, `ContestSubmission` schema (H-00) — frozen, correctly shaped, zero rows ever written by any code path.
- `DisputeOutcome`, `CuratedFeedbackLabel` schema (H-00) — same: frozen, correctly shaped (H-20 audit confirmed `CuratedFeedbackLabel.approved_for_training`/`label_quality`/`version` are exactly what an explicit curation step would need), zero rows ever written.

**DEFERRED** (explicit PO decision on record this session, with a named blocker):
- H-09 (document upload), H-11 (evidence-document-required submit block), H-13 (outbox writer), H-14 (idempotency/dedup), H-15 (attempt history), H-16 Clause A (webhook-triggered outbox reconciliation), H-17 Clause B (accept-path invocation), H-19 (outcome feedback writer), H-21 (test/sandbox mode) — all deferred on the single identical, independently-re-verified blocker: no real Razorpay HTTP client exists, and H-17 Clause B additionally has no documented contract to build against at all.
- H-22's three remaining metrics (submission success rate, API errors, won/lost/closed outcome metrics) — deferred on the same blocker, decided in the H-22 implementation authorization itself.

**No new partial/dormant/deferred item was found in Modules A-G this turn** beyond what H's own audit sequence already surfaced — Modules B, C, D's newly-verified code (§2) is fully wired and active, not dormant.

## 4. Residual H work versus substantial future infrastructure

This is the specific question the prior four-module draft answered by *assertion* (recommending "new module" framing) rather than by *analysis*. Re-examined here on its own terms:

**SOURCE-EXPLICIT** distinction available in the E-F-G-H blueprint itself: §12.1 "Hackathon Priority" separates MUST ("H: safe contest package + dry-run/test-mode external action + audit") from STRONG ("Outbox retries/reconciliation and won/lost feedback loop"). The blueprint's own priority table treats the *safety package* (already built, H-06/07/08/10/12/18) as the MUST-have core of Module H, and treats *outbox retries/reconciliation* and the *feedback loop* as separable, lower-priority additions — a structural signal, not this report's invention, that the deferred cluster was always conceived as an extension layer rather than an unfinished piece of H's core.

**YOUR RECOMMENDATION**, built on that signal plus repository evidence:

| Deferred item | Residual-H or new-infrastructure? | Reasoning |
|---|---|---|
| H-09 (document upload), H-11 (submit block) | Residual H — small, tightly coupled to the outbox writer | Each is a single check/call inside the same outbox-dispatch code path; neither has independent shape as a "module" |
| H-13 (outbox writer), H-14 (idempotency), H-15 (attempt history) | **Substantial new infrastructure** | Together these require a net-new HTTP client, credential management, retry/backoff logic, and a new Celery task family — a different engineering surface than anything in Modules A-H (first outbound third-party credential in the project) |
| H-16 Clause A, H-21 | Residual H — config/reconciliation details of the same outbox writer | Not independently shaped; naturally implemented alongside H-13 |
| H-17 Clause B | Blocked on missing source material, not an infrastructure-size question — cannot be classified as "residual" or "new module" until a contract exists |
| H-19 (outcome writer) | **Substantial new infrastructure**, but only once H-13's family exists | Needs its own webhook-resolution logic against real submissions; not meaningfully smaller than "a module," but strictly sequenced after the H-13 family |
| H-22 remaining 3 metrics | Residual — trivial additions to the already-built `queue_metrics.py` pattern once H-13/H-19 exist | Confirmed by the H-22 implementation itself, which structured the metrics file for exactly this additive extension |

Net: only **two** genuinely infrastructure-sized clusters exist in the entire deferred set — (a) the Razorpay outbox/client family (H-09/11/13/14/15/16A/21), and (b) the outcome-webhook/curation family (H-19, plus H-20's already-satisfied status becoming re-testable). This is a narrower and more precise finding than the prior report's four-module draft, which had already reached a similar conclusion (Module J and Module K) but without this explicit residual-vs-new test.

## 5. Master-blueprint requirements with no current module owner

**SOURCE-EXPLICIT**, cross-referenced against every existing document (master blueprint, A-B-C doc, D doc, E-F-G-H doc) — none of which assigns these to any letter, current or historical:

- Frontend (master §9) — not owned by any A-H document; not RAG/G, not H (H owns the *approval action*, not its UI).
- Real authentication (master §12) — partially adjacent to H-04 (RBAC), but H-04's own scope (confirmed in this session's H-04 verification) is authorization given an already-identified user, not authentication itself.
- Deployment/MLOps (master §13, §20) — not owned by any module; Module F trains models but no document assigns it responsibility for experiment tracking or containerization.
- Runtime/infra observability (master §13's latency/error-rate metrics) — not owned by H-22, whose scope (confirmed by its own frozen PO decision) is specifically human-review-queue metrics, not system/infra metrics.

## 6. Should Frontend, External Action Integration, Outcome Feedback, Deployment/MLOps exist as future modules?

**YOUR RECOMMENDATION** (not source-explicit — the source material stops at "Outcome Feedback" with no further structure, as established in the prior research report §2.2 and re-confirmed unchanged in this pass):

| Candidate | Recommend as a distinct future module? | Why |
|---|---|---|
| Frontend | **Yes** | Genuinely unowned (§5), zero dependency on any other undone work, highest demonstration value, matches master §9 in full |
| External Action Integration (Razorpay client + outbox execution) | **Yes**, scoped to only the documented contract | Genuinely substantial new infrastructure (§4), not residual H work; must explicitly exclude the accept path pending a documented contract |
| Outcome Feedback (outcome webhooks + curation loop) | **Yes**, but only as a module that starts *after* External Action Integration exists | Hard-blocked by definition (§3); not schedulable as parallel or earlier work |
| Deployment/MLOps | **Yes**, but this one is more accurately "hardening/packaging" than a product-capability module — it adds no new business capability, only makes existing capabilities reproducible/observable | Genuinely unowned (§5), zero external dependency, but qualitatively different in kind from the other three (infrastructure around the product, not a new pipeline stage) |

This reaffirms — through a more granular test this time (§4-§5) rather than by structural analogy alone — the same four capability clusters the prior draft already proposed. The difference this pass makes is procedural, not substantive: no letters are assigned below (per explicit instruction), and Deployment/MLOps is explicitly flagged as categorically different from the other three (see §9).

## 7. Preserve A-O naming, or establish new post-H numbering?

**YOUR RECOMMENDATION**, with the deciding evidence restated plainly:

- Preserving the master document's own A-O naming verbatim is **not viable**: its own letters I, J, K, L, M, N, O no longer describe unbuilt work — I/J/K/L are fully inside Module F, M is fully inside Module G, N is Module H, and O is split between "already satisfied" (H-20) and "deferred" (H-19). Continuing the master's naming would require calling new work by letters that already mean something else in the shipped system, which is a direct correctness hazard for anyone reading the docs later (a "Module M" reference could mean either the master blueprint's Grounded LLM Response Generation, already built, or a hypothetical new module — genuinely ambiguous).
- The E-F-G-H blueprint's own naming provides no forward letters at all — it ends at "Outcome Feedback" with no successor identifier.
- Therefore: **do not reuse A-O.** A new, explicitly post-H numbering scheme is the only option that avoids collision. This report deliberately does not propose what that scheme looks like (roman numerals continuing from H, decimal sub-numbers like H.1/H.2, or an entirely new prefix) — that choice is presentational and belongs to the PO, not to this research.

## 8. Dependency graph of genuinely remaining product capabilities

```
Module H (CLOSED / READY)
   |
   |-- Frontend
   |     depends on: A-H APIs only (all already stable)
   |     blocks: nothing else in this graph
   |
   |-- External Action Integration (contest draft/submit + document upload only)
   |     depends on: H-00 frozen schema + H-06/07/08/10/12 dormant gates
   |     external dependency: real Razorpay sandbox credentials (outside all capabilities)
   |     excludes: accept-path (no documented contract — dead end until new source material exists)
   |     |
   |     v
   |   Outcome Feedback (webhook ingestion + DisputeOutcome + curation loop)
   |     depends on: External Action Integration's populated ContestSubmission rows
   |     re-opens: H-20's "already satisfied by absence" status for mandatory re-audit once this exists
   |
   |-- Deployment / MLOps hardening
         depends on: nothing functionally; packages whatever exists at build time
         soft-benefits-from: Frontend (to containerize `web` per master §20.1), External Action Integration (a more complete demo)
```

**External dependency outside every capability in this graph**: real (or sandbox) Razorpay API credentials — the same conclusion reached independently across every H-cluster audit this session and in the prior research report; repeated here because it remains the single hard external gate on the entire external-action side of the graph.

## 9. Smallest coherent set of future modules

**YOUR RECOMMENDATION**, directly answering the "do not assume exactly four" instruction:

The evidence in §1-§8 supports **four** capability clusters, not by construction but because four is what's actually left after removing everything already implemented (§2) and everything correctly deferred with a named blocker (§3): Frontend, External Action Integration, Outcome Feedback, and Deployment/MLOps. This report checked explicitly for a smaller or larger set and found:

- **Could it be three?** Only by merging Deployment/MLOps into one of the other three — rejected, because Deployment/MLOps has zero functional dependency on the others (§8) and touches an entirely different concern (packaging vs. new business capability); merging it would blur a real distinction, not simplify one.
- **Could it be five or more?** Only by splitting External Action Integration and Outcome Feedback each into two — considered and rejected: within External Action Integration, H-09/11/13/14/15/16A/21 have no independent shape from each other (§4's residual-vs-new test found no internal seam); within Outcome Feedback, webhook ingestion and the curation step are tightly coupled by the same H-20 non-negotiable ("never automatically retrain") and don't benefit from separate tracking.
- **Could Deployment/MLOps itself split** into "Deployment" and "MLOps/Observability"? Plausible, and flagged as a genuine open question rather than resolved here — master §13 (MLOps) and §20 (Deployment) are separate sections with different content (experiment tracking vs. container orchestration), and nothing in this research forces them together except convenience. This is the one point where this report is less certain than the rest, and it should be treated as a live packaging question for the PO rather than a settled recommendation.

So: **four clusters, with one explicitly-flagged uncertainty** (whether Deployment and MLOps/Observability should be one module or two) — not the confident "exactly four" of the prior draft, but landing in the same place after an actual test for over/under-splitting, which the prior draft did not run explicitly.

## 10-11. On not assuming four, and not assigning letters

Addressed throughout: §9 shows the four-cluster count is a result, not an assumption, and includes the one place a fifth split is plausible. No letters are assigned anywhere in this document, per instruction 11 — every candidate above is referred to by name only. Letter/number assignment is explicitly left to the PO's response to §7.

---

## Summary table (every finding, one place, by label)

| # | Finding | Label |
|---|---|---|
| 1 | Master blueprint defines modules A-O; E-F-G-H blueprint ends its own named sequence at "Outcome Feedback" with no successor letter | SOURCE-EXPLICIT |
| 2 | Master's I/J/K/L (cost-sensitive eval, calibration, policy decision, explainability) are fully inside repo Module F | ALREADY IMPLEMENTED |
| 3 | Master's M (grounded LLM generation) is fully inside repo Module G | ALREADY IMPLEMENTED |
| 4 | Master's N (human-in-the-loop approval) is repo Module H | ALREADY IMPLEMENTED |
| 5 | Master's O (outcome feedback) is split: reconciliation-webhook half already satisfied (H-16 Clause B via Module A/H-06), outcome-write half deferred (H-19) | ALREADY IMPLEMENTED (part) / DEFERRED (part) |
| 6 | Six external-action safety gates (H-06/07/08/10/12/18) are correct and tested but uncalled by any live path | DORMANT |
| 7 | H-00's outbox/submission/outcome/curation schema is correctly shaped but unpopulated | DORMANT |
| 8 | H-09/11/13/14/15/16A/17B/19/21/22-remainder are deferred on one identical, re-verified blocker (no Razorpay client); H-17B additionally has no documented contract | DEFERRED |
| 9 | Frontend, real auth, rate limiting, correlation IDs, MLOps/experiment tracking, runtime observability, full Docker deployment, README | MISSING |
| 10 | Modules A-D, B specifically, are fully implemented and match master's own field-level requirements | ALREADY IMPLEMENTED |
| 11 | Dead-letter intent is satisfied by the existing `ProcessingError` table, not a literal queue mechanism | ALREADY IMPLEMENTED |
| 12 | Malware scanning is a real deterministic MVP scanner, matching master's own "at minimum" fallback language | ALREADY IMPLEMENTED |
| 13 | Only two deferred clusters are infrastructure-sized (external-action family, outcome-feedback family); the rest of the deferred H-items are residual to those two | YOUR RECOMMENDATION |
| 14 | Four future capability clusters (Frontend, External Action Integration, Outcome Feedback, Deployment/MLOps), reached by an explicit over/under-splitting test, with one open question (should Deployment and MLOps/Observability split into two) | YOUR RECOMMENDATION |
| 15 | Do not reuse the master's A-O letters for new work; a new post-H scheme is required, shape left to the PO | YOUR RECOMMENDATION |
| 16 | No module letters assigned in this document | (procedural — per explicit instruction) |

---

**End of report. No further action taken.**
