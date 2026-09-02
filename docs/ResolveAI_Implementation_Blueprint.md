<!-- Converted from the original DOCX for IDE-agent readability. Embedded diagrams are represented by their figure captions; structured tables and text are preserved. -->

ResolveAI

AI-Powered Chargeback Evidence Verifier & Auto-Responder

Detailed Implementation Flow and Engineering Blueprint

Razorpay Hackathon Track: AI Risk Manager

Prepared as a student implementation guide

# Document Purpose and Source Alignment

This document converts the proposed ResolveAI concept into an implementation-ready engineering plan. It is intended to guide a student team from repository initialization through a working defense-only prototype, held-out evaluation, live demonstration, and deployment.

| Hackathon requirement traceability — The attached AI Risk Manager brief asks teams to stop merchant losses from fraud, returns or chargebacks by building a working detector, verifier or auto-responder for one class of loss. It explicitly requires measured precision and recall on a held-out test set, honest reporting of false-positive cost, and a defense-only solution. ResolveAI selects chargebacks as the single loss class and makes evidence verification the measurable AI task. |
| --- |

| Brief requirement | ResolveAI interpretation | Implementation evidence |
| --- | --- | --- |
| One class of loss | Chargebacks / payment disputes | All scoring, data schema and metrics are scoped to dispute contestability. |
| Working detector/verifier/auto-responder | Verifier + decision engine + grounded response generator | End-to-end case ingestion, evidence validation, three-way decision and response draft. |
| Precision and recall | Primary metrics for safe contest recommendation | Locked held-out test report with confusion matrix and PR-AUC. |
| False-positive cost | Unsafe contest recommendation is explicitly costed | Validation-set threshold optimized for expected financial loss. |
| Defense-only | No fraud-enablement or adversarial offensive logic | Human approval before contest action; logging and evidence grounding. |

# 1. Solution Scope

## 1.1 Product goal

ResolveAI helps a merchant evaluate a chargeback case before contesting it. The system verifies whether required evidence exists, extracts and cross-checks facts across documents and transaction records, estimates contestability using a calibrated cost-sensitive model, explains the recommendation, and—only for appropriate cases—generates a grounded evidence response for human approval.

## 1.2 Primary measurable ML task

Binary modelling label for evaluation: “safe-to-contest based on available evidence and policy context” versus “not safe-to-contest / insufficient evidence”. The product exposes a three-way operational decision—ACCEPT, HUMAN REVIEW, CONTEST—using thresholds selected on validation data.

## 1.3 Non-goals for the hackathon MVP

- Predicting whether a consumer is fraudulent.

- Automating a financial decision without human control.

- Training a large foundation model from scratch.

- Building a generic chatbot as the main experience.

- Claiming production Razorpay performance without organizer-provided production data.

- Using offensive methods to simulate or optimize fraud attacks.

# 2. Target User Roles and Primary Workflows

| Role | Responsibility | Primary screens/actions |
| --- | --- | --- |
| Merchant Admin | Configure account, team and integrations | Settings, API/webhook configuration, permissions. |
| Risk Analyst | Review disputes and evidence quality | Dispute queue, case workspace, explanations, missing evidence. |
| Approver | Make final business decision | Approve contest, accept dispute, request more evidence. |
| System / Worker | Process events and documents asynchronously | OCR, extraction, validation, scoring, response generation. |
| Model Maintainer | Reproduce and compare model versions | Experiment tracking, test report, registry, drift dashboard. |

# 3. End-to-End Runtime Flow

*Figure 1. ResolveAI reference architecture.*

| # | Stage | Detailed responsibility |
| --- | --- | --- |
| 1 | Dispute created | Webhook or demo event enters the system. Verify authenticity, validate schema and generate an idempotency key. |
| 2 | Case created | Persist immutable dispute snapshot and create the case state machine. |
| 3 | Context enrichment | Load payment, order, shipment, refund, customer-history and merchant-history data. |
| 4 | Evidence intake | Collect uploaded evidence and record file hash, MIME type, size, source and upload timestamp. |
| 5 | Document intelligence | Preprocess, OCR and extract normalized structured fields with confidence and provenance. |
| 6 | Reason-code retrieval | Load the evidence policy/checklist relevant to the dispute reason. |
| 7 | Deterministic validation | Check file completeness, amount/date/order/tracking consistency and temporal constraints. |
| 8 | Feature build | Construct versioned tabular features from evidence, consistency and historical context. |
| 9 | ML inference | Predict calibrated probability that the case is safely contestable. |
| 10 | Cost/policy decision | Apply threshold policy and hard rules to produce ACCEPT, HUMAN REVIEW or CONTEST. |
| 11 | Explanation | Produce reason codes, evidence gaps, SHAP feature contributions and expected-loss interpretation. |
| 12 | Response generation | For CONTEST candidates, generate a strictly evidence-grounded response; reject unsupported statements. |
| 13 | Human approval | Analyst/approver reviews recommendation and evidence package. |
| 14 | Contest/accept action | Use demo integration or permitted dispute API action after approval. |
| 15 | Outcome capture | Record won/lost/accepted outcomes for evaluation and future retraining. |

# 4. Case State Machine

```text
NEW -> ENRICHING -> EVIDENCE_PENDING -> PROCESSING_DOCUMENTS -> VERIFYING -> SCORED -> REVIEW_REQUIRED | CONTEST_CANDIDATE | ACCEPT_RECOMMENDED -> APPROVED -> SUBMITTED -> WON | LOST | CLOSED
```

Every transition must be persisted with timestamp, actor, previous state, new state, reason and model/policy version where applicable. Repeated webhook delivery must not create duplicate cases or duplicate transitions.

| Transition | Allowed when | Failure behavior |
| --- | --- | --- |
| NEW → ENRICHING | Validated new dispute event | Send to dead-letter queue if required identifiers are missing. |
| ENRICHING → EVIDENCE_PENDING | Payment/order context loaded | Mark partial context and route for review if integration unavailable. |
| EVIDENCE_PENDING → PROCESSING_DOCUMENTS | Minimum document set uploaded or analyst triggers analysis | Reject unsupported formats and retain case state. |
| PROCESSING_DOCUMENTS → VERIFYING | OCR/extraction completed | Low OCR confidence becomes a quality flag, not silent acceptance. |
| VERIFYING → SCORED | Feature contract passes | Schema mismatch blocks inference. |
| SCORED → decision state | Model + policy engine complete | Uncertain or hard-rule conflict forces HUMAN REVIEW. |
| APPROVED → SUBMITTED | Authorized approver explicitly confirms | No automatic submission in MVP. |

# 5. Module-by-Module Implementation

## 5.1 Module A — Secure Dispute Ingestion

Purpose: create exactly one auditable internal case per incoming dispute event.

| Input | Processing | Output |
| --- | --- | --- |
| Webhook payload or demo event JSON | Signature/auth validation; schema validation; timestamp freshness; idempotency check; safe logging | dispute record, event record, case ID, queued enrichment job |

### Implementation logic

- Expose POST /webhooks/razorpay (or /webhooks/demo for offline mode).

- Read raw request body before JSON parsing so signature verification is possible.

- Reject invalid signatures with 401/400 and never enqueue work.

- Construct idempotency key from provider event identifier, or deterministic hash of immutable event fields if a demo event lacks one.

- Store raw payload encrypted or minimally redacted; store normalized fields in relational columns.

- Acknowledge webhook quickly, then execute enrichment asynchronously.

```text
NormalizedDisputeEvent = {
  event_id, dispute_id, payment_id, merchant_id, amount, currency,
  reason_code, phase, status, created_at, respond_by, received_at
}
```

| Acceptance test — Posting the same valid event 10 times creates one dispute case and 10/1 event-receipt audit entries according to the chosen idempotency design, but never duplicates downstream contest actions. |
| --- |

## 5.2 Module B — Transaction and Order Enrichment

Purpose: build a canonical case context so evidence is checked against trusted transaction records rather than in isolation.

| Source | Fields to normalize | Fallback |
| --- | --- | --- |
| Payment | payment_id, amount, currency, method, status, paid_at | Use fixture/demo API if live provider data unavailable. |
| Order | order_id, SKU/product summary, quantity, billed amount, order_time | Merchant CSV/demo database. |
| Shipment | courier, tracking_id, dispatch_time, delivered_at, address token, delivery state | Optional; missing shipment becomes an evidence gap. |
| Refund | refund_id, amount, status, initiated_at, completed_at | No refund row = explicit “not found”, not null ambiguity. |
| History | tokenized customer/merchant aggregate features | Aggregate only; avoid raw sensitive attributes. |

```text
CaseContext = {
  dispute: {...}, payment: {...}, order: {...}, shipment: {...},
  refunds: [...], customer_history: {...}, merchant_history: {...},
  documents: [...]
}
```

## 5.3 Module C — Evidence Intake and File Security

Purpose: safely accept evidence files and preserve provenance.

- Allowed formats: PDF, PNG, JPEG; add configurable size and page-count limits.

- Validate MIME type using file content, not only extension.

- Generate SHA-256 hash; deduplicate identical evidence within a case.

- Persist storage key rather than raw binary in the relational database.

- Run malware/file safety scanning where available; at minimum reject executable/polyglot formats for MVP.

- Capture uploader, upload time, original name, sanitized name, source, file hash and document type prediction.

- Never expose object-storage paths directly to clients; use authorized download endpoints or pre-signed URLs.

## 5.4 Module D — Document Intelligence Pipeline

Purpose: convert unstructured evidence into normalized facts with confidence and provenance.

```text
File -> render pages -> image cleanup -> OCR -> layout/text blocks -> document type -> field extraction -> normalization -> confidence -> provenance
```

| Sub-step | Implementation | Output |
| --- | --- | --- |
| Preprocessing | Deskew, orientation correction, resolution normalization, light denoise; preserve original | page images + quality metrics |
| OCR | PaddleOCR/docTR or equivalent | text blocks, bounding boxes, OCR confidence |
| Doc classification | Rules or lightweight classifier using text/layout cues | invoice / POD / tracking / refund / communication / other |
| Field extraction | Regex + key-value heuristics + optional small LLM structured extraction | raw field/value/provenance tuples |
| Normalization | Currency/date/order/tracking canonicalization | normalized fields |
| Quality scoring | coverage + OCR confidence + missing critical fields | document_quality_score |

```text
ExtractedField = {
  document_id, field_name, raw_value, normalized_value,
  page, bbox, extraction_method, confidence
}
```

| Grounding rule — No downstream response generator may use a document fact unless it exists in the structured extraction store or another trusted transaction source, with an attached provenance reference. |
| --- |

## 5.5 Module E — Reason-Code Policy / RAG Knowledge Base

Purpose: determine what evidence is required or useful for a particular chargeback reason without asking the LLM to invent policy.

| Data object | Example fields |
| --- | --- |
| reason_code_policy | reason_code, category, description, version, effective_from, source_ref |
| evidence_requirement | reason_code, evidence_type, requirement=required/recommended, validation_rule |
| policy_chunk | policy_id, chunk_text, embedding, metadata, version |

- Use PostgreSQL + pgvector for the MVP so transactional metadata and embeddings are versioned together.

- Retrieve by exact reason code first; semantic retrieval is a fallback for explanatory policy text, not a replacement for deterministic mappings.

- Attach policy version to every decision so results are reproducible.

- Maintain a “policy not found” path that forces human review.

## 5.6 Module F — Cross-Document Consistency Engine

Purpose: detect contradictions and compute deterministic evidence features before ML inference. This is one of the strongest differentiators of the solution.

| Rule family | Example check | Severity / output |
| --- | --- | --- |
| Amount | invoice_total ≈ payment_amount within configured tolerance | PASS / WARN / FAIL + delta |
| Order identity | invoice_order_id = internal order_id | PASS/FAIL |
| Tracking | document tracking_id = shipment tracking_id | PASS/FAIL |
| Customer/entity | normalized name/address similarity above threshold | score + WARN if ambiguous |
| Timeline | order <= payment <= shipment <= delivery <= dispute | PASS/FAIL + violated edge |
| Refund | claim context versus successful refund record | conflict flag |
| Evidence coverage | required evidence types found | coverage ratio |
| Document quality | critical fields readable and confidence adequate | quality score |

```text
ValidationResult = {
  rule_id, case_id, status, severity, observed, expected,
  score, evidence_refs[], explanation
}
```

Hard failures should be explicit and configurable. Example: a required proof-of-delivery file missing may block automatic CONTEST even if the ML score is high. Soft warnings become model features or review annotations.

## 5.7 Module G — Feature Engineering Contract

Purpose: create a stable, versioned feature vector that can be reproduced in training and inference.

| Feature group | Representative features |
| --- | --- |
| Evidence availability | has_invoice, has_pod, has_tracking, has_customer_communication, has_refund_record |
| Coverage/quality | required_coverage, recommended_coverage, avg_ocr_conf, critical_field_coverage, unreadable_page_ratio |
| Consistency | amount_match, amount_delta_pct, order_id_match, tracking_match, customer_similarity, timeline_violation_count |
| Temporal | payment_to_ship_hours, ship_to_delivery_hours, delivery_to_dispute_days, hours_to_deadline |
| Dispute | reason_code, phase, amount_log, currency, evidence_count |
| History | customer_order_count, prior_dispute_rate, merchant_dispute_rate_30d, merchant_loss_rate_90d |

| Train-serving consistency — Build one shared feature library used by both offline training and the inference API. Never re-implement feature logic separately in notebooks and backend code. |
| --- |

## 5.8 Module H — ML Training and Model Selection

*Figure 2. Model development pipeline with a locked held-out test set.*

### Dataset split

Prefer a grouped temporal split. Order cases by time, keep later cases for validation/test, and group repeated merchant/customer/template identifiers where possible to reduce leakage. If the provided dataset is small, use grouped cross-validation inside the training partition while preserving the final locked test partition.

TRAIN (oldest ~70%) | VALIDATION (~15%) | HELD-OUT TEST (newest ~15%)

### Required model ladder

| Model | Purpose | Decision criterion |
| --- | --- | --- |
| Rules-only | Operational baseline | Shows whether ML adds measurable value. |
| Logistic Regression | Transparent statistical baseline | Good calibration reference and sanity check. |
| Random Forest | Nonlinear tree baseline | Useful comparison; monitor probability calibration. |
| LightGBM / CatBoost | Primary tabular model | Choose based on validation PR-AUC, precision/recall and expected cost, not accuracy alone. |
| Hybrid rules + model | Recommended production-style decision | Hard controls + calibrated model + human review band. |

## 5.9 Module I — Cost-Sensitive Evaluation

The hackathon brief explicitly asks teams to report false-positive cost. Define the positive class as “safe to contest automatically/with minimal review”. A false positive means recommending CONTEST when the evidence is actually insufficient or unsafe. This is the expensive error.

ExpectedCost(t) = C_FP * FP(t) + C_FN * FN(t)

Choose threshold t* on VALIDATION data, not on the test set.

For a stronger business formulation, use case-level exposure:

ExpectedLoss_i = P(error_i) × financial_exposure_i × business_cost_multiplier_i

| Metric | Why it is required |
| --- | --- |
| Precision | How often a CONTEST recommendation is actually safe/correct. |
| Recall | How many safe contest opportunities are captured. |
| F1 | Balances precision and recall but ignores monetary asymmetry. |
| PR-AUC | More informative than accuracy/ROC-AUC under class imbalance. |
| False-positive count/rate | Direct operational safety indicator. |
| False-positive cost | Explicitly aligns with the brief. |
| Brier score / ECE | Checks whether predicted probabilities are trustworthy. |
| Expected avoided loss | Secondary business estimate; must state assumptions. |

## 5.10 Module J — Probability Calibration

Raw boosted-tree probabilities can be overconfident. Fit Platt scaling or isotonic regression using validation data only. Store the calibrator as part of the deployable model package. Compare reliability curves and Brier score before/after calibration.

```text
raw_model_score -> calibrator -> p_contestable -> policy engine
```

## 5.11 Module K — Three-Way Policy Decision

*Figure 3. Three-way policy decision flow.*

Do not hard-code final thresholds before validation. The policy configuration should contain threshold values, hard blockers and review overrides.

```text
if hard_blocker: HUMAN_REVIEW or ACCEPT
elif p < T_accept: ACCEPT
elif p < T_contest: HUMAN_REVIEW
else: CONTEST_CANDIDATE
```

| Policy input | Examples |
| --- | --- |
| Calibrated probability | p_contestable |
| Hard blockers | missing required evidence, policy not found, critical mismatch, low extraction confidence |
| Financial exposure | dispute amount / expected recoverable amount |
| Deadline risk | hours remaining before respond_by |
| Model applicability | supported reason code / known feature schema / drift status |

## 5.12 Module L — Explainability

Each case must return two types of explanation: deterministic evidence reasoning and model contribution. This avoids treating SHAP as the only explanation.

| Explanation layer | Example |
| --- | --- |
| Evidence rules | “Payment amount matches invoice; proof of delivery present; tracking ID matches shipment.” |
| Evidence gaps | “Signed POD missing; customer-name match only 0.79.” |
| Model factors | Top SHAP contributors that increased/decreased contestability. |
| Decision policy | “p=0.91 exceeded T_contest=0.86 and no hard blocker was triggered.” |
| Financial context | “Case amount ₹X; false-positive cost assumption Y.” |

## 5.13 Module M — Grounded LLM Response Generation

The LLM is a constrained language-generation layer, not the decision maker. It receives only verified facts and retrieved policy text. Its job is to assemble a concise evidence response and list supporting attachments.

```text
VerifiedFacts + EvidenceRefs + ReasonPolicy + ResponseTemplate -> LLM -> StructuredDraft -> GroundingChecker -> Human Review
```

### Structured generation contract

```text
{
  "summary": "...",
  "claims": [
    {"text":"...", "evidence_refs":["doc_1:p1:delivery_date"], "fact_ids":["fact_22"]}
  ],
  "attachments": ["doc_1", "doc_4"],
  "missing_evidence": [],
  "warnings": []
}
```

### Guardrails

- No claim without at least one fact/evidence reference.

- No new amount, date, identity or tracking value not present in structured facts.

- Reject draft if the response contradicts consistency-engine results.

- Use an allow-list of permissible case fields for prompt construction.

- Store prompt template version, retrieved policy IDs and model/provider metadata for audit.

- Never place secret keys or raw unnecessary PII in the prompt.

## 5.14 Module N — Human-in-the-Loop Approval

MVP principle: CONTEST means “candidate for contest”, not “automatically submitted”. The approver sees the original dispute, extracted facts, evidence files, missing evidence, model confidence, explanation, generated response and policy references before acting.

| Action | Required control |
| --- | --- |
| Approve contest | Role permission + explicit confirmation + audit entry. |
| Send to review | Assignee/reason captured. |
| Accept dispute | Reason captured; model recommendation retained for later error analysis. |
| Override model | Mandatory override reason; useful for future model improvement. |
| Request evidence | Create task/checklist tied to missing requirement. |

## 5.15 Module O — Outcome Feedback Loop

When final dispute outcomes become available, append them to the outcome store rather than mutating historical predictions. This preserves the exact information that was available at decision time.

```text
prediction_snapshot + human_action + submitted_response + final_outcome -> training_candidate
```

Retraining is not required during the hackathon. What matters is demonstrating that the data model supports future supervised learning and post-deployment monitoring.

# 6. Database Design

Use PostgreSQL as the system of record. Store large files in MinIO/S3-compatible object storage. Use pgvector only for policy/document embeddings that genuinely need semantic retrieval.

| Table | Key fields |
| --- | --- |
| merchants | id, name, status, settings_json, created_at |
| users | id, merchant_id, role, email_hash, status |
| customers | id, merchant_id, external_hash, aggregate fields |
| payments | payment_id, merchant_id, amount, currency, method, status, paid_at |
| orders | order_id, merchant_id, amount, customer_id, created_at |
| shipments | id, order_id, tracking_id, courier, dispatched_at, delivered_at |
| refunds | id, payment_id, amount, status, timestamps |
| disputes | dispute_id, payment_id, reason_code, phase, amount, status, respond_by |
| dispute_events | event_id, dispute_id, type, received_at, payload_hash |
| documents | id, dispute_id, type, storage_key, sha256, mime, quality_score |
| extracted_fields | id, document_id, field_name, value_norm, confidence, page, bbox |
| evidence_requirements | id, reason_code, evidence_type, requirement, version |
| validation_results | id, dispute_id, rule_id, status, severity, score, evidence_refs |
| feature_snapshots | id, dispute_id, feature_version, features_json, created_at |
| risk_predictions | id, dispute_id, model_version, raw_score, calibrated_score, decision |
| generated_responses | id, dispute_id, prompt_version, draft_json, grounding_status |
| human_decisions | id, dispute_id, user_id, action, reason, created_at |
| outcomes | id, dispute_id, final_status, recovered_amount, closed_at |
| audit_logs | id, merchant_id, actor, action, entity_type, entity_id, metadata, created_at |
| model_versions | version, feature_version, metrics_json, artifact_uri, status |

## 6.1 Important constraints

- Unique constraint on disputes.dispute_id.

- Unique constraint on provider event_id when available.

- Foreign keys between dispute → payment → order/customer context where data exists.

- Immutable prediction snapshots: insert new prediction version; do not overwrite old decisions.

- Row-level merchant isolation enforced in application queries and tests.

- Index dispute status, respond_by, merchant_id, payment_id and created_at for dashboard performance.

- Embedding rows carry policy/version metadata so retrieval is reproducible.

# 7. Backend API Contracts

| Method | Route | Purpose | Input | Output |
| --- | --- | --- | --- | --- |
| POST | /webhooks/razorpay | Receive/verify dispute event | Provider event | 202 Accepted / validation error |
| GET | /api/disputes | List/filter queue | status, reason, deadline, pagination | summary rows |
| GET | /api/disputes/{id} | Case workspace data | case id | full authorized case |
| POST | /api/disputes/{id}/documents | Upload evidence | multipart + declared type | document record |
| POST | /api/disputes/{id}/analyze | Start OCR/verification/scoring | case id | job id |
| GET | /api/jobs/{id} | Async job status | job id | progress/error |
| GET | /api/disputes/{id}/analysis | Return verification + score | case id | facts, flags, score, explanations |
| POST | /api/disputes/{id}/response | Generate grounded draft | case id + selected evidence | draft JSON |
| POST | /api/disputes/{id}/decision | Human action | action + reason | decision audit record |
| POST | /api/disputes/{id}/submit | Submit approved contest in supported mode | approval token | provider/demo result |
| GET | /api/metrics/model | Evaluation dashboard | model version | held-out metrics |
| GET | /api/audit | Authorized audit search | filters | audit events |

## 7.1 API engineering requirements

- Use Pydantic schemas for every request/response.

- Return stable machine-readable error codes, not only strings.

- Use correlation/request IDs across API, worker and audit logs.

- Paginate collection endpoints.

- Keep analysis endpoints asynchronous because OCR/LLM processing can exceed request timeouts.

- Enforce RBAC at the endpoint and service layers.

- OpenAPI generated from the FastAPI contract becomes part of the demo deliverable.

# 8. Asynchronous Processing and Reliability

Use Celery + Redis for the hackathon if the team is comfortable with it. A simpler FastAPI background worker can be used for the first vertical slice, but queue-backed workers provide stronger retry and visibility behavior.

```text
ingest_event -> enrich_case -> process_documents -> validate_evidence -> build_features -> score_case -> generate_explanations -> optional_generate_response
```

| Concern | Implementation rule |
| --- | --- |
| Retries | Retry only transient errors; do not blindly retry validation failures. |
| Idempotency | Each job receives a case ID + stage/version key; completed stage is not duplicated. |
| Dead-letter | Persist unrecoverable job failures for analyst visibility. |
| Timeout | OCR/LLM calls have explicit timeout and bounded retry. |
| Partial completion | Store per-document status so one bad file does not erase successful extraction of others. |
| Observability | Track queue depth, job duration, failure rate and provider latency. |

# 9. Frontend Implementation Flow

| Screen | Must show | Primary action |
| --- | --- | --- |
| Login / role landing | User role and merchant context | Enter authorized workspace |
| Risk Command Center | Open disputes, amount at risk, deadlines, decision distribution | Open priority case |
| Dispute Queue | Status, amount, reason, deadline, evidence score, recommendation | Filter/sort/open case |
| Case Workspace | Original dispute, timeline, documents, extracted facts, validation, score, explanation | Analyze / review evidence |
| Response Review | Grounded claims with evidence references, attachments, warnings | Approve/edit/reject draft |
| Model Metrics | Held-out precision, recall, PR-AUC, confusion matrix, FP cost, calibration | Explain system quality to judges |
| Audit / Activity | Case transitions, user actions, model/policy versions | Trace decision history |

| Demo UX priority — The case workspace and held-out metrics screen matter more than decorative dashboards. Judges should be able to see evidence, reasoning, confidence and cost in a single path. |
| --- |

# 10. Dataset and Benchmark Construction

## 10.1 If organizer data is provided

- Create a data dictionary before modelling.

- Identify what label genuinely represents contestability; do not silently equate “won” with evidence sufficiency without analysis.

- Remove post-outcome fields that would leak the answer.

- Use temporal/grouped splitting and preserve the final test set.

- Document class balance, missingness, label definition and exclusions.

## 10.2 If no real dispute dataset is provided

Build a controlled synthetic benchmark that tests the verifier, but label it honestly as synthetic/controlled. Generate structured case facts first, then render evidence documents from templates so ground truth is known.

```text
Case generator -> ground-truth transaction facts -> document templates -> controlled corruptions / omissions -> verifier label -> split by template family + time seed
```

| Scenario family | Positive / negative construction |
| --- | --- |
| Complete valid evidence | All required evidence present; amounts/IDs/timeline consistent. |
| Missing required evidence | Remove one or more reason-specific required artifacts. |
| Amount mismatch | Invoice and payment disagree beyond tolerance. |
| Order mismatch | Evidence references a different order ID. |
| Tracking mismatch | POD/tracking does not correspond to shipment. |
| Timeline contradiction | Delivery occurs after dispute or impossible chronology. |
| Low-quality document | Blur/rotation/compression reduces OCR reliability. |
| Partial refund conflict | Refund data changes contestability for the case scenario. |
| Ambiguous identity | Near-match customer names/addresses requiring review. |

## 10.3 Robustness test matrix

| Perturbation | Levels | Measure |
| --- | --- | --- |
| Blur | none / mild / strong | field extraction recall; final decision recall |
| Rotation | 0° / 2° / 5° / 90° | OCR recovery rate |
| Compression | high / medium / low quality | critical field coverage |
| Crop | none / edge crop / critical crop | hard-failure behavior |
| Missing page | complete / one page absent | evidence completeness |
| OCR noise | synthetic substitutions | consistency-engine resilience |

# 11. Testing Strategy

| Layer | Tests |
| --- | --- |
| Unit | normalizers, amount/date rules, similarity functions, feature builders, cost function, threshold policy |
| Schema/contract | Pydantic request-response contracts, feature schema, model artifact compatibility |
| Integration | webhook → DB; upload → storage → OCR; case → score; response → grounding checker |
| Security | invalid signature, wrong role, cross-merchant access, unsafe file type, oversized upload |
| Idempotency | repeated webhook, repeated analyze request, repeated submit action |
| ML | split leakage checks, deterministic feature generation, held-out metrics, calibration |
| Robustness | poor document quality and missing/contradictory evidence |
| End-to-end | one positive contest case + one accept case + one human-review case |

## 11.1 Definition of done for the ML system

- Locked test set cannot be loaded by training scripts unless an explicit evaluation command is used.

- Model comparison table is generated from reproducible experiment artifacts.

- Thresholds are selected only from validation data.

- Final report includes confusion matrix and monetary false-positive analysis.

- Every deployed model artifact contains feature schema version and calibrator.

- Inference refuses incompatible/missing feature versions rather than filling silently.

# 12. Security and Fintech Controls

| Control area | Required implementation |
| --- | --- |
| Authentication | JWT/session authentication; secure password handling if local auth is used. |
| Authorization | RBAC with merchant-level data isolation; deny-by-default. |
| Webhook integrity | Verify provider signature and enforce idempotency. |
| Secrets | Environment/secret store; never hard-code API keys in repository. |
| PII minimization | Tokenize/hash where identity is not required; do not log sensitive fields. |
| File handling | MIME validation, size limits, safe filenames, private object storage. |
| Transport | HTTPS in deployed environment. |
| Database | Parameterized ORM/queries, least-privilege user, backups for demo environment where practical. |
| Audit | Append-only action and model-decision records. |
| LLM privacy | Send minimum necessary verified fields; no secrets/raw unnecessary PII. |
| Human control | No automatic contest submission in MVP. |

# 13. MLOps and Observability

| Capability | MVP implementation |
| --- | --- |
| Experiment tracking | MLflow or structured runs directory containing params, metrics, split hash and artifact URI. |
| Model registry | model_versions table + artifact directory; mark champion model explicitly. |
| Data/version trace | dataset hash, split manifest, feature version and policy version. |
| Runtime metrics | inference latency, OCR latency, LLM latency, queue duration, error rate. |
| Decision metrics | contest/review/accept distribution; override rate; unsupported reason-code rate. |
| Quality monitoring | feature missingness, score distribution, calibration drift once outcomes exist. |
| Reproducibility | single configuration file/environment lock + seeded training where applicable. |

# 14. Recommended Repository Structure

resolveai/
├── apps/
│   ├── api/                 # FastAPI entrypoint and routes
│   └── web/                 # React/Next.js frontend
├── services/
│   ├── ingestion/
│   ├── enrichment/
│   ├── documents/           # OCR, classification, extraction
│   ├── validation/          # consistency rules
│   ├── features/            # shared train/serve features
│   ├── risk/                # model loading, calibration, policy
│   ├── rag/                 # reason-code retrieval
│   ├── response/            # grounded LLM generation
│   └── audit/
├── ml/
│   ├── data/
│   ├── splits/
│   ├── train.py
│   ├── evaluate.py
│   ├── calibrate.py
│   └── reports/
├── workers/
│   └── tasks.py
├── db/
│   ├── models/
│   ├── migrations/
│   └── seeds/
├── contracts/               # Pydantic/domain schemas shared by services
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── e2e/
├── infra/
│   ├── docker/
│   └── compose.yaml
├── docs/
│   ├── architecture/
│   ├── data_dictionary.md
│   └── demo_script.md
├── .env.example
├── pyproject.toml / requirements.txt
└── README.md

| Architecture rule — Keep business logic out of API route functions. Routes validate/authenticate; services implement use cases; repositories handle persistence; workers execute long-running stages. |
| --- |

# 15. Recommended Implementation Sequence

The team should build a vertical slice early, then increase intelligence. A perfect ML notebook without an integrated case flow is not sufficient for this hackathon.

| Priority | Work package | Definition of completion |
| --- | --- | --- |
| P0-1 | Freeze label, schema and demo scenarios | Data dictionary; case schema; positive/negative label definition; three demo cases. |
| P0-2 | Repository + infrastructure | FastAPI, PostgreSQL, migrations, frontend shell, Docker Compose, seed script. |
| P0-3 | Case ingestion and queue | Webhook/demo endpoint, dispute list, case detail, idempotency. |
| P0-4 | Evidence upload | Object storage, metadata, file validation, case attachment UI. |
| P0-5 | OCR + extraction | At least invoice + POD/tracking extraction with provenance. |
| P0-6 | Consistency rules | Amount/order/tracking/timeline/coverage checks. |
| P0-7 | Rules-only decision | First working ACCEPT/REVIEW/CONTEST flow before ML. |
| P1-1 | Training pipeline | Split manifest, baseline models, experiment tracking. |
| P1-2 | Primary tabular model | LightGBM/CatBoost + validation metrics. |
| P1-3 | Calibration + cost thresholds | Calibrated probability, false-positive cost optimization. |
| P1-4 | Integrate inference | Shared feature contract, prediction persistence, model version. |
| P1-5 | Explainability | Evidence rationale + SHAP/top factors. |
| P1-6 | Reason-code RAG | Policy/version store and retrieval. |
| P1-7 | Grounded response | Structured draft + evidence references + grounding checks. |
| P2-1 | Metrics dashboard | Held-out results, confusion matrix, cost and robustness. |
| P2-2 | Security hardening | RBAC, cross-merchant isolation tests, secrets, audit. |
| P2-3 | Outcome loop | Won/lost fixture/webhook and immutable outcome capture. |
| P2-4 | Demo polishing | Priority queue, deadline UI, response review, failure handling. |
| P2-5 | Final reproducibility | One-command local launch, seed data, model artifact, README and smoke tests. |

# 16. Team Parallelization

| Student / role | Owns | Integration contract |
| --- | --- | --- |
| Backend engineer | FastAPI, DB, webhooks, RBAC, audit, async jobs | Publishes case/document/analysis APIs and schemas. |
| ML engineer | dataset split, features, models, calibration, cost threshold, SHAP | Publishes model artifact + inference function + metrics JSON. |
| Document/GenAI engineer | OCR, extraction, policy retrieval, grounded response | Publishes extracted-field schema and response contract. |
| Frontend/product engineer | queue, case workspace, metrics, response review | Consumes stable APIs; does not embed business rules in UI. |
| Optional QA/DevOps owner | Docker, tests, CI, observability, demo fixtures | Maintains one-command reproducible build. |

If only three students are available, combine Backend+DevOps, ML, and Document/GenAI+Frontend. The first integration contract to freeze should be the canonical CaseContext and AnalysisResult schemas.

# 17. Canonical Service Contracts

## 17.1 AnalysisResult

```text
AnalysisResult = {
  case_id,
  evidence: {required_coverage, quality_score, missing_types[]},
  validations: [{rule_id, status, severity, score, evidence_refs[]}],
  model: {version, raw_score, calibrated_score, feature_version},
  policy: {version, hard_blockers[], T_accept, T_contest},
  decision: "ACCEPT" | "HUMAN_REVIEW" | "CONTEST",
  explanations: {evidence_reasons[], model_factors[], financial_context},
  created_at
}
```

## 17.2 Case priority

priority_score = w1 * deadline_urgency + w2 * normalized_amount + w3 * review_need + w4 * evidence_readiness

Use this only for queue ordering. Do not confuse queue priority with contestability probability.

# 18. Final Demonstration Flow

Prepare three deterministic demo cases. Judges should see that the system can say “no” or “review”, not only “contest”.

| Case | Designed outcome | What to demonstrate |
| --- | --- | --- |
| A — Strong evidence | CONTEST | Invoice + POD + tracking agree; high calibrated score; grounded response generated. |
| B — Contradictory evidence | HUMAN REVIEW | Amount/order mismatch; model cannot bypass hard validation; explanation clearly shows conflict. |
| C — Missing required evidence | ACCEPT / REQUEST EVIDENCE | Required evidence absent; system refuses unsafe contest recommendation. |

1. Open Risk Command Center and show amount at risk + response deadlines.

1. Open Case A; upload or reveal evidence; trigger analysis.

1. Show OCR-extracted fields with source references.

1. Show consistency checks and evidence-completeness score.

1. Show calibrated probability, threshold and explanation.

1. Generate response; click a claim to highlight its evidence source.

1. Show human approval step; use demo submit action if live integration is not permitted.

1. Open model metrics page and show held-out precision, recall, PR-AUC, confusion matrix and false-positive cost.

1. Open Case B or C to prove the system rejects unsafe automation.

# 19. Final Evaluation Report Template

The final numbers below must be populated from the locked test set; do not pre-fill aspirational values.

| Metric | Rules | LogReg | Random Forest | LightGBM/CatBoost | Hybrid |
| --- | --- | --- | --- | --- | --- |
| Precision |  |  |  |  |  |
| Recall |  |  |  |  |  |
| F1 |  |  |  |  |  |
| PR-AUC |  |  |  |  |  |
| False positives |  |  |  |  |  |
| False-positive cost |  |  |  |  |  |
| Brier score |  |  |  |  |  |
| Median inference latency |  |  |  |  |  |

## 19.1 Required result commentary

- State test set size and positive-class prevalence.

- Report absolute TP/FP/FN/TN counts in addition to percentages.

- Explain the business assumption behind C_FP and C_FN.

- Show how validation threshold changes the precision-recall trade-off.

- Report clean-document and degraded-document performance separately if robustness tests are used.

- State dataset limitations and whether data is real, organizer-provided or synthetic.

# 20. Deployment Blueprint

```text
Browser -> Nginx/HTTPS -> React/Next.js + FastAPI -> PostgreSQL
                                    |-> Redis/Celery workers
                                    |-> MinIO/S3 evidence store
                                    |-> ML artifact / MLflow
                                    |-> Optional LLM provider/local endpoint
```

## 20.1 Docker Compose services for hackathon

| Service | Responsibility |
| --- | --- |
| web | Frontend |
| api | FastAPI |
| worker | Celery worker |
| postgres | System-of-record database + pgvector |
| redis | queue/cache |
| minio | evidence object storage |
| mlflow (optional) | experiment tracking |

Provide seed fixtures and one command such as docker compose up --build. A separate initialization command should apply migrations, seed reason-code policies, create demo users/cases and register the chosen model artifact.

# 21. Major Risks and Mitigations

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| No real labeled chargeback data | Limits claims about production performance | Use controlled benchmark; disclose synthetic nature; keep organizer-data adapter ready. |
| Label ambiguity | Won/lost may not equal evidence sufficiency | Define measurable verifier label explicitly and document assumptions. |
| OCR errors | Can create false contradictions | Store confidence/provenance; low-confidence fields trigger review. |
| Data leakage | Can inflate hackathon metrics | Grouped temporal split; remove post-outcome fields; split manifest checks. |
| LLM hallucination | Unsafe financial statement | Generate only from verified facts; claim-to-evidence grounding checker. |
| Over-automation | Defense-only/safety concern | Human approval before provider action. |
| Integration failure during demo | Can break otherwise good prototype | Offline demo adapter uses same internal service contract as live provider adapter. |
| Too much UI work | Reduces engineering depth | Prioritize case workspace + metrics + one clean dashboard. |

# 22. Final Submission Checklist

☐  One clearly defined loss class: chargebacks.

☐  Verifier label and positive class documented.

☐  At least one rules-only baseline and one ML baseline implemented.

☐  Locked held-out test set and split manifest preserved.

☐  Precision and recall reported on held-out test set.

☐  False-positive cost reported with assumptions.

☐  PR-AUC and confusion matrix included.

☐  Calibration or confidence-quality analysis included.

☐  Three-way ACCEPT/REVIEW/CONTEST decision implemented.

☐  At least one hard evidence blocker implemented.

☐  OCR extraction includes provenance.

☐  Cross-document consistency checks implemented.

☐  Reason-code evidence policy versioned.

☐  LLM response uses verified facts only.

☐  Claim/evidence grounding is visible in UI.

☐  Human approval required before contest submission.

☐  Audit log records model/policy versions and human overrides.

☐  Webhook/demo ingestion is idempotent.

☐  RBAC and merchant isolation tested.

☐  Docker-based reproducible startup works.

☐  Three demo cases cover contest, review and accept/missing-evidence outcomes.

☐  README explains setup, architecture, dataset limitations and demo steps.

# Appendix A — Traceability from Hackathon Bar to Engineering Evidence

| Hackathon bar | Where demonstrated |
| --- | --- |
| Working detector/verifier/auto-responder | Case workspace: document verification + ML decision + grounded response. |
| Measured precision and recall | Model Metrics page and locked evaluation report. |
| Held-out test set | Split manifest + evaluate.py + test-set report. |
| False-positive cost | Cost-sensitive threshold analysis and dashboard. |
| Defense-only | Human approval, audit trail, no offensive fraud-generation capability. |

# Appendix B — Recommended Technology Stack

| Layer | Recommendation | Why |
| --- | --- | --- |
| Frontend | React or Next.js | Modern component UI; strong placement relevance. |
| Backend | FastAPI + Pydantic | Typed contracts, async-friendly, auto OpenAPI. |
| Database | PostgreSQL | Reliable relational system of record. |
| Vector | pgvector | Avoid separate vector infrastructure for MVP. |
| Queue | Celery + Redis | OCR/LLM/background job orchestration. |
| Object storage | MinIO / S3 | Private evidence storage. |
| OCR | PaddleOCR / docTR | Document text and layout extraction. |
| Tabular ML | LightGBM / CatBoost | Strong for heterogeneous tabular risk features. |
| Explainability | SHAP + deterministic rules | Model + evidence-level explanations. |
| Experiment tracking | MLflow | Metrics/model artifact traceability. |
| Testing | Pytest | Unit, integration, security, E2E. |
| Deployment | Docker Compose | Fast reproducible hackathon deployment. |

# Appendix C — Recommended Student Talking Points for Interview

- Why accuracy is insufficient for imbalanced financial-risk tasks.

- How false-positive cost changes threshold selection.

- Why probability calibration matters before using confidence operationally.

- How temporal/grouped splitting reduces leakage.

- Why deterministic evidence rules and ML should be combined rather than choosing only one.

- How a shared feature contract prevents train-serving skew.

- Why the LLM is placed after verification rather than before it.

- How idempotency and auditability affect webhook-driven fintech systems.

- How a human-review band handles uncertainty and out-of-distribution cases.

- How the outcome store would support future retraining and drift monitoring.

| Recommended build philosophy — Finish one complete vertical slice—event → evidence → verification → decision → explanation → human action—before expanding model sophistication. The judging bar rewards a working, honestly measured defense system more than a collection of disconnected AI components. |
| --- |
