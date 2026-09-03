# Next Four Modules — Research Report

Status: DRAFT — for Product Owner review. No implementation authorized by this document.
Prepared against repository checkpoint `23160a1` (branch `claude/resolveai-audit-h05-4tp9ns`), immediately following the Module H MVP closure audit.

## 1. Current Project State

- **Repository**: `Ronit069/Resolve_AI`, branch `claude/resolveai-audit-h05-4tp9ns`, HEAD `23160a191c6d3d8a23deec24dd7bb45299c72ffa` (short `23160a1`), working tree clean, matches `origin`.
- **Implemented, backend-only, no frontend**: Modules A through H are implemented per the repository's own `docs/ResolveAI_Modules_E_F_G_H_Technical_Blueprint.md` and `docs/ResolveAI_Modules_A_B_C_Requirements_and_Database_Design.md`. There is no `frontend/`, `web/`, or `apps/` directory anywhere in the repository — confirmed by directory listing and re-confirmed during the H-21 audit (Module H closure sequence).
- **Module H MVP checkpoint**: formally closed and marked READY in the immediately preceding closure audit (this session). Every H-00 through H-22 requirement has an explicit disposition (IMPLEMENTED / ALREADY SATISFIED / DEFERRED-BLOCKED); the deferred cluster (H-09, H-11, H-13, H-14, H-15, H-16 Clause A, H-17 Clause B, H-19, H-21, and three of H-22's six metrics) is uniformly blocked on one missing piece: a real Razorpay HTTP client and its populated persistence lineage (`ExternalActionOutbox`, `ContestSubmission`, `DisputeOutcome`).
- **No frontend, no MLflow/experiment-tracking tooling, no Dockerfile for the API/worker/frontend services, and an empty `README.md`** (0 lines) — confirmed this turn by direct inspection. `docker-compose.yaml` at the repo root only defines three infrastructure services (`postgres`, `redis`, `minio`); it does not define `api`, `worker`, or `web` services despite the master blueprint's Section 20 specifying all of them.
- **Test suite**: 494 passing / 2 known pre-existing failures (both `FileNotFoundError: 'artifacts'` in Module F, unrelated to this research) as of the last full run at this checkpoint (from the Module H closure audit); this report did not rerun the suite (research-only task, no code changed).

## 2. Blueprint Roadmap

This is the single most consequential finding of this research and governs everything in Phases 3 and 5 below — **presented as fact, not interpretation, because it is a direct quote of source material.**

### 2.1 Two blueprint documents define two different "A-O" roadmaps

`docs/ResolveAI_Implementation_Blueprint.md` (the top-level, earliest-authored master document, §5) defines fifteen modules **A through O**:

> A: Secure Dispute Ingestion · B: Transaction and Order Enrichment · C: Evidence Intake and File Security · D: Document Intelligence Pipeline · E: Reason-Code Policy / RAG Knowledge Base · F: Cross-Document Consistency Engine · G: Feature Engineering Contract · H: ML Training and Model Selection · I: Cost-Sensitive Evaluation · J: Probability Calibration · K: Three-Way Policy Decision · L: Explainability · M: Grounded LLM Response Generation · N: Human-in-the-Loop Approval · O: Outcome Feedback Loop

`docs/ResolveAI_Modules_E_F_G_H_Technical_Blueprint.md` — the later, more detailed document that the actual repository was built against (confirmed by matching every H-01 through H-22 requirement audited across this entire session to this document, not the master one) — **redefines and compresses** modules E through H, and states its own architecture alignment explicitly at line 4:

> "Architecture alignment: Modules A -> B -> C -> D -> E -> F -> G -> H -> Outcome Feedback"

Its own §1.1 "End-to-End Sequence" describes:
- **Module E** = "resolves reason-code evidence requirements, validates completeness, links equivalent fields, detects contradictions and creates an immutable validation/feature snapshot" — this is the master document's **F (Cross-Document Consistency Engine) + G (Feature Engineering Contract) combined**, not its E (RAG).
- **Module F** = "consumes only versioned feature snapshots, produces a calibrated contestability probability, computes cost-sensitive ACCEPT/REVIEW/CONTEST recommendation and stores explanations" — this is the master document's **H (ML Training) + I (Cost-Sensitive Evaluation) + J (Calibration) + K (Policy Decision) + L (Explainability) combined**. Confirmed against the repository: `app/models/module_f.py` (`RiskPrediction.calibrated_probability`, `.recommendation`, `.hard_block`) and `PredictionExplanation` (SHAP), plus the extensive `test_module_f_step4.py` through `test_module_f_step14.py` suite covering baselines, LightGBM/CatBoost, calibration, and cost-sensitive thresholding — all already implemented.
- **Module G** = "retrieves applicable reason-code/policy guidance and generates a structured contest draft" — this is the master document's **E (RAG Knowledge Base) + M (Grounded LLM Response Generation) combined**.
- **Module H** = "places the case in a reviewer queue... records an explicit human action... After approval, an idempotent external-action outbox uploads evidence documents... Outcome webhooks update the case... and feed outcome labels back into evaluation" — this is the master document's **N (Human-in-the-Loop Approval) + O (Outcome Feedback Loop) combined**, plus the outbox/idempotency machinery the master document did not fully specify (H-06 through H-15).

**Consequence (source-derived fact, not this report's opinion): the master blueprint's own Modules I, J, K, and L — Cost-Sensitive Evaluation, Probability Calibration, Three-Way Policy Decision, and Explainability — are already fully implemented, as part of the repository's Module F**, and are not available or appropriate content for "the next four modules" despite sharing those letters in the earlier document. Re-implementing them under new letters would duplicate existing, tested, frozen work — expressly prohibited by this task's own design-discipline rule 3 ("Do not 'improve' Modules A-H").

### 2.2 What the authoritative document says comes after Module H

The E-F-G-H blueprint's own roadmap terminates its named sequence at **"Outcome Feedback"** (line 4) — it does not assign this a module letter of its own. Its content is described in §1.1 step 8 ("Outcome webhooks update the case to under_review/won/lost/closed and feed outcome labels back into evaluation and later retraining") and its §12 "Recommended Implementation Order" step 11 ("Add outcome webhook reconciliation and curated feedback label tables") — and this is exactly the content already given H-item numbers **inside** Module H's own requirement table (H-16 State reconciliation, H-19 Outcome feedback, H-20 Training feedback safety), all audited this session. H-19 and part of H-16/H-21 are DEFERRED/BLOCKED; H-20 is ALREADY SATISFIED (by absence).

**This is a genuine roadmap ambiguity, documented rather than silently resolved, per this task's explicit instruction:** the source material does not define a "Module I" (or J/K/L) in any form. There is no textual roadmap entry beyond "Outcome Feedback," and that content is already partially housed inside Module H's own numbering. Everything proposed below as Modules I-L is therefore **this report's own reasoned construction**, built only from (a) content the master blueprint calls for but which no module A-H owns (frontend, deployment, MLOps/observability), and (b) the E-F-G-H blueprint's own explicitly-deferred H-cluster items, promoted to full-module status because their remaining scope (a real external HTTP client, retry/outbox execution, and a webhook-driven feedback loop) is substantial, distinct, cross-cutting engineering work rather than a residual H-item fix. Section 7 below lists this promotion itself as a PO decision.

### 2.3 Other roadmap-relevant source material

- Master blueprint §9 "Frontend Implementation Flow" specifies seven screens (Login/role landing, Risk Command Center, Dispute Queue, Case Workspace, Response Review, Model Metrics, Audit/Activity) and explicit per-screen content — entirely unbuilt.
- Master blueprint §13 "MLOps and Observability" calls for experiment tracking (MLflow), a model registry, drift monitoring, and reproducibility tooling — none of this exists in the repository (confirmed: zero MLflow references anywhere).
- Master blueprint §20 "Deployment Blueprint" and §20.1 specify `web`, `api`, `worker`, `postgres`, `redis`, `minio`, and optional `mlflow` Compose services — the repository's actual `docker-compose.yaml` defines only the three infrastructure services.
- Master blueprint §22 "Final Submission Checklist" includes "Docker-based reproducible startup works" and "README explains setup, architecture, dataset limitations and demo steps" — the repository's `README.md` is a zero-byte file.
- E-F-G-H blueprint §12.1 "Hackathon Priority" explicitly marks "Outbox retries/reconciliation and won/lost feedback loop" as STRONG (not MUST) and "Automated retraining/drift alert UI" as OPTIONAL — both were correctly deferred through H's audit sequence, consistent with the source's own priority.

## 3. Candidate Modules

### Module I — Frontend / Merchant & Reviewer Web Application
- **Objective**: give the roles defined in master §2 (Merchant Admin, Risk Analyst, Approver) a working UI over the existing, stable backend API surface built in Modules A-H.
- **Authoritative requirements**: master blueprint §9 (screen table), §2 (roles), §18 (Final Demonstration Flow), §22 (submission checklist).
- **Dependencies on previous modules**: consumes A-H's existing API contracts only (`review.py`'s workspace/review-action endpoints, `observability.py`'s queue-metrics endpoint, generation/validation/evidence endpoints) — no backend changes required to start.
- **Dependencies on other candidate modules**: none structurally required; benefits from Module L's deployment packaging once built.
- **Repository readiness**: zero frontend code exists; all consumed endpoints already exist and are tested.
- **Missing infrastructure**: an entire frontend application (framework choice, build tooling, auth flow against the existing `X-User-Id`-header MVP auth).
- **Can it be implemented now?** Yes, for read/approve/decision screens against existing endpoints. No, for any screen implying a capability the backend doesn't have yet (e.g., a "submit to Razorpay" button — the backend endpoint it would call doesn't exist; a "won/lost outcome" display — the data doesn't exist yet).
- **External integration dependency**: none — purely internal API consumption.
- **Blueprint/repository contradictions**: none found; this is unambiguously unbuilt, blueprint-required work.
- **PO authorization needed for**: technology choice (master §Appendix B recommends React/Next.js but does not mandate it), auth strategy (keep the MVP `X-User-Id` header approach vs. build real session/JWT auth — master §12 calls for "JWT/session authentication," which the current backend does not implement at all).

### Module J — Real External-Action Integration (Razorpay Client + Outbox Execution)
- **Objective**: complete the deferred H-cluster by building the actual Razorpay HTTP client, the outbox dispatch worker, and wiring the six already-implemented, currently-dormant H-06/H-07/H-08/H-10/H-12 gates (plus the still-unbuilt H-09/H-11) into one real call chain, exactly as the H-13 PO decision described as its future scope.
- **Authoritative requirements**: E-F-G-H blueprint §6.3 H-09, H-11, H-13, H-14, H-15, H-16 Clause A, H-17 Clause B, H-21; §12 step 10 ("Implement Razorpay document/contest integration behind an external-action outbox in test/dry-run mode first"); §15 "Current Razorpay Integration Notes" (the only documented external contract: `PATCH /v1/disputes/:id/contest` with draft/submit actions, `POST /v1/documents` with `purpose=dispute_evidence`, contest amount/summary constraints).
- **Dependencies on previous modules**: entirely built on frozen Module H schema (H-00's `ExternalActionOutbox`, `ContestSubmission`, `ContestPackage`, `RazorpayDocumentLink` tables) and the six already-implemented, dormant gates — no changes to those gates required, only a new orchestrating caller.
- **Dependencies on other candidate modules**: none blocks it from starting; Module K depends on it.
- **Repository readiness**: the safety-gate half is fully built, tested, and dormant, ready to be composed; the persistence schema is frozen and ready to be populated.
- **Missing infrastructure**: the actual HTTP client, credential/config management (test-mode default per H-21), retry/backoff logic, the outbox dispatch worker (Celery task), and document-upload orchestration.
- **Can it be implemented now?** Only partially, and only for what §15 documents: contest draft/submit (H-13/H-14/H-15/H-09/H-11) and document upload. **The Razorpay dispute-accept endpoint (H-17 Clause B) is not documented anywhere in the repository's source material** — this was flagged as an explicit gap in the H-17 audit and remains unresolved; this report does not invent one.
- **External integration dependency**: yes — the single largest blocker in the whole deferred cluster. Requires real Razorpay API credentials (test/sandbox mode per H-21) that do not currently exist in `app/core/config.py`.
- **Blueprint/repository contradictions**: none — this module's scope was explicitly deferred by name across seven separate PO decisions this session (H-09, H-11, H-13, H-14, H-15, H-16, H-17, H-19, H-21), all citing the identical missing-client root cause, independently re-verified each time.
- **PO authorization needed for**: whether to build this at all before real Razorpay sandbox credentials are available; the accept-path gap (build only contest draft/submit, or seek/derive an accept contract first); H-21's test-mode-default config shape.

### Module K — Outcome Feedback & Training-Label Curation Loop
- **Objective**: ingest real won/lost/closed outcome webhooks against submitted disputes, populate `DisputeOutcome` (H-9), and build the explicit curation step that lets `CuratedFeedbackLabel` (H-10 schema) feed future ML datasets — completing the roadmap's terminal "Outcome Feedback" node and H-19/H-20.
- **Authoritative requirements**: E-F-G-H blueprint line 4 ("-> Outcome Feedback"), §1.1 step 8, §12 step 11, H-19, H-20; master blueprint §5.15 Module O, §13 (quality/drift monitoring once outcomes exist).
- **Dependencies on previous modules**: hard dependency on Module J — H-19's own deferral (confirmed independently three times this session: H-19, H-20, and this report) is specifically that there is no `ContestSubmission`/`ContestPackage` row for any outcome webhook to link against; that lineage is Module J's output, not something K can create for itself.
- **Dependencies on other candidate modules**: depends on J; independent of I and L.
- **Repository readiness**: schema is fully frozen and ready (`DisputeOutcome`, `CuratedFeedbackLabel`); zero write-path code exists, by design (the H-19/H-20 PO decisions explicitly forbade building an orphaned writer ahead of J).
- **Missing infrastructure**: an outcome-webhook endpoint/handler (separate from Module A's dispute-status webhook, or an extension of it — undecided, see §7), the `DisputeOutcome` writer, and the curation workflow/UI or process that sets `CuratedFeedbackLabel.approved_for_training`.
- **Can it be implemented now?** No — blocked on Module J exactly as H-19 was blocked on H-13.
- **External integration dependency**: yes, transitively through Module J (outcome webhooks are part of the same Razorpay integration).
- **Blueprint/repository contradictions**: none.
- **PO authorization needed for**: everything — this entire module is DEFERRED pending Module J, consistent with the standing "no synthetic scaffolding ahead of a real consumer" rule applied throughout the H-item audits.

### Module L — Deployment, MLOps & Observability Hardening
- **Objective**: close the gap between "backend passes its own test suite" and the master blueprint's own definition of a complete hackathon deliverable — one-command reproducible startup, experiment tracking/model registry, and operational documentation.
- **Authoritative requirements**: master blueprint §13 (MLOps and Observability), §20 (Deployment Blueprint), §22 (Final Submission Checklist: "Docker-based reproducible startup works", "README explains setup, architecture, dataset limitations and demo steps"), P2-5 ("One-command local launch, seed data, model artifact, README and smoke tests").
- **Dependencies on previous modules**: packages whatever exists at the time it's built (A-H at minimum; ideally I-K too, for a complete one-command demo) — but its infrastructure pieces (Dockerfiles for `api`/`worker`, MLflow wiring, README) do not themselves require I/J/K to exist first.
- **Dependencies on other candidate modules**: soft/packaging dependency only, not a functional blocker — could start in parallel with I/J/K.
- **Repository readiness**: `docker-compose.yaml` exists for infra only; no Dockerfiles anywhere in the repository; `model_versions`-equivalent tracking exists only as raw `ModelVersion`/`ModelDecisionPolicy` rows (Module F), with no MLflow or registry UI; README is empty.
- **Missing infrastructure**: `Dockerfile`s for the API and worker (and frontend, once Module I exists), extended `docker-compose.yaml`, MLflow (or equivalent) integration, a written README, and smoke-test/seed scripts (master mentions a "separate initialization command" for migrations/seed/model registration that does not currently exist).
- **Can it be implemented now?** Yes, largely — this is infrastructure/documentation work around already-implemented modules, not new business logic, and requires no external API contract at all.
- **External integration dependency**: none.
- **Blueprint/repository contradictions**: none.
- **PO authorization needed for**: MLflow vs. the "structured runs directory" fallback the master blueprint explicitly allows ("MLflow **or** structured runs directory containing params, metrics, split hash and artifact URI" — master §13); how much of Module F's existing ad-hoc training scripts (`train_lightgbm_comparator.py`, `calibrate_and_optimize.py` etc., at the repo root) to fold into this vs. leave as-is (folding them risks "improving Module F," which is out of scope).

## 4. Dependency Graph

```
Module H (CLOSED / READY)
   |
   |-- Module I (Frontend)         -- depends on: A-H APIs only (no I/J/K/L dependency)
   |
   |-- Module J (External Action)  -- depends on: H-00 schema + H-06/07/08/10/12 gates (frozen, dormant)
   |        |                          external dependency: real Razorpay sandbox credentials (OUTSIDE all 4 modules)
   |        v
   |    Module K (Outcome Feedback) -- depends on: Module J's populated ContestSubmission/outcome lineage
   |
   |-- Module L (Deployment/MLOps)  -- soft/packaging dependency on I/J/K; no hard functional dependency
```

**Dependencies on infrastructure outside all four modules**: Module J (and transitively K) requires real Razorpay API credentials/sandbox access, which is an external, non-code prerequisite no module can create for itself — this is the same conclusion reached independently in every H-cluster audit this session (H-09 through H-21).

## 5. Repository Gap Analysis

| Module | Already exists | Missing | Must be added | Must NOT be changed |
|---|---|---|---|---|
| I | All backend endpoints it would consume (review.py, generation.py, evidence.py, observability.py, validation.py) | Any frontend code, build tooling, real session auth | New `frontend/` (or `apps/web/`) project only | No backend endpoint contracts, no Module A-H code |
| J | H-00 frozen schema (`ExternalActionOutbox`, `ContestSubmission`, `ContestPackage`, `RazorpayDocumentLink`); H-06/07/08/10/12 dormant gates | Razorpay HTTP client, credentials/config, outbox dispatch worker, H-09/H-11 document-upload logic | New service module(s) calling the existing gates in sequence + new Celery task(s) | The six existing gate functions' internal logic (H-06/07/08/10/12/18), `review.py`'s H-03/H-04/H-05/H-18 logic, H-00 schema shape |
| K | H-00 frozen schema (`DisputeOutcome`, `CuratedFeedbackLabel`) | Outcome webhook ingestion, `DisputeOutcome` writer, curation workflow | New webhook handler + writer + (later) a curation process/UI | H-20's "already satisfied by absence" status must be re-audited, not silently assumed satisfied, once this module writes real rows (explicitly noted in the H-20 PO decision's own re-audit trigger) |
| L | `docker-compose.yaml` (infra only); ad-hoc training scripts at repo root | Dockerfiles, MLflow/registry, README, seed/smoke scripts | New `Dockerfile`s, extended compose file, `README.md`, docs | Existing training scripts' actual training/evaluation logic (Module F is frozen); existing `docker-compose.yaml` service definitions for postgres/redis/minio (extend, don't replace) |

## 6. Cross-Module Risks

- **Schema conflicts**: Module J and K both write to H-00's already-frozen tables (`ExternalActionOutbox`, `ContestSubmission`, `DisputeOutcome`); the schema itself must not change — only new INSERT/UPDATE code paths are added. Any perceived schema inadequacy discovered during J/K design must be raised as a PO decision, not silently patched via an ad-hoc migration.
- **Migration issues**: none anticipated for J/K (schema pre-exists); Module L's Dockerfile/compose work has zero migration surface.
- **Authorization gaps**: Module I must decide whether to keep the MVP `X-User-Id`-header pseudo-auth or build real authentication — using the frontend to bypass RBAC (e.g., a client-trusted role claim) would violate H-04's "deny-by-default" RBAC principle and master SH-03 ("common authentication, RBAC and merchant isolation"). This is a PO decision, not something to resolve unilaterally in a frontend PR.
- **External API assumptions**: Module J is the highest-risk module in this set specifically because it's the first module in the whole project to require a live/sandboxed third-party credential. Building it against assumed-but-unverified sandbox behavior (rather than the two things §15 actually documents: the contest and document endpoints) would violate this task's explicit "do not infer undocumented Razorpay endpoints" rule — most acutely for the accept path (H-17 Clause B), which has no documented endpoint at all.
- **Concurrency/idempotency**: Module J must reuse H-06's `.populate_existing()` fresh-read pattern and H-14's outbox idempotency-key design (already schema-frozen) rather than inventing a new concurrency model.
- **Auditability**: Module J/K actions must write through the existing `AuditLog`/`ReviewAction`/`DisputeEvent` audit trail conventions, not a parallel logging mechanism.
- **Tenant isolation**: Module I's frontend must never bypass the existing `merchant_id`-scoped query pattern used uniformly across every A-H endpoint; any new J/K endpoint must scope by merchant the same way.
- **Security**: Module J introduces the project's first outbound credential to a financial third party — secrets management (env/secret store, never committed) becomes newly load-bearing in a way it wasn't for A-H's inbound-only webhook model.
- **Architectural drift**: none of the four proposed modules touches Modules A-H's existing code; the only drift risk is Module L accidentally "cleaning up" Module F's ad-hoc training scripts, which this report explicitly flags as out of scope unless the PO authorizes it.

## 7. PO Decisions Required

1. **Naming/numbering**: accept this report's proposal to use letters I/J/K/L for genuinely new post-H modules (since the master document's own I-L are already implemented inside Module F, and the E-F-G-H blueprint names no letter beyond H at all) — or choose different names/tracking entirely. This report does not silently decide this; it is flagged here per the "if the blueprint uses different names/numbers... explain the mapping" instruction.
2. **Module J vs. "finish Module H"**: should the remaining H-cluster items (H-09, H-11, H-13, H-14, H-15, H-16 Clause A, H-17 Clause B, H-19, H-21, H-22 remainder) be tracked as a new Module J, or reopened as direct continuations of Module H's own numbering? This report recommends new-module framing because the remaining scope is a distinct engineering concern (a live external client) rather than a residual gap in already-shipped H logic, but this is a process/tracking choice for the PO, not an engineering fact.
3. **Module J's accept-path gap**: build only the documented contest draft/submit + document-upload integration and leave H-17 Clause B formally unresolved, or invest in independently sourcing a documented Razorpay accept contract before any accept-path code is written? No accept endpoint may be inferred.
4. **Module J's credential/environment**: who provisions real (or sandbox) Razorpay credentials, and on what timeline — this report treats their absence as an external blocker no module can resolve internally.
5. **Module I's authentication model**: keep the MVP header-based pseudo-auth for the hackathon demo, or build real session/JWT auth (master §12 calls for it, but it was never built for A-H either) before or alongside the frontend?
6. **Module I's technology stack**: React/Next.js per master Appendix B, or PO preference.
7. **Module K's webhook design**: extend Module A's existing `razorpay_webhook` endpoint to also carry outcome events, or add a dedicated outcome-webhook endpoint? Module A is frozen and must not be casually redesigned; this choice affects whether K needs any (even additive) touch to Module A.
8. **Module L's experiment tracking**: MLflow (heavier, matches master's primary recommendation) or the master-blueprint-sanctioned lighter alternative ("structured runs directory") — a real cost/benefit choice, not an engineering fact.
9. **Sequencing**: confirm or override the recommended sequence in §8 below — in particular, whether Module I (frontend) should be prioritized above Module J (external action) given I has no external blocker and delivers immediate demo value, versus J unlocking the largest number of previously-deferred H-items.

## 8. Recommended Sequence

**I → L → J → K**, with L's non-Dockerfile pieces (README, MLflow decision) able to run in parallel with I at any time.

Rationale:
1. **Module I first**: zero external dependency, consumes only already-stable, already-tested A-H APIs, and is the single highest-leverage gap for demonstrating the system at all (per master's own "Demo UX priority" callout in §9 and the Final Demonstration Flow in §18) — every other candidate module is invisible without it.
2. **Module L next (or in parallel)**: also zero external dependency, directly serves the master blueprint's own "Definition of Done"/Final Submission Checklist items (reproducible startup, README), and de-risks Module J by giving it a real deployable target to integrate into rather than a bespoke local setup.
3. **Module J third**: the highest-risk module (first live external credential in the project), and the one most exposed to the "do not invent undocumented API contracts" constraint — it should not be started until the PO has explicitly resolved decisions 2-4 in §7, and building I/L first buys time for that without blocking on it.
4. **Module K last**: hard-blocked on Module J by definition (§3, §4); cannot be meaningfully started before J produces real `ContestSubmission` rows to react to.

This sequence deliberately does **not** front-load Module J despite it unlocking the largest number of previously-deferred H-items, because doing so would repeat the exact risk this entire H-item audit sequence was built to avoid: building toward an external integration before its PO-authorized shape and credentials exist.
