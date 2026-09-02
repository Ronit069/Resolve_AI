# ResolveAI - Modules E, F, G and H
Evidence Validation -> Risk ML -> RAG/LLM -> Human Review & Contest
Technical Requirements, Database Design, Integration Contracts and Definition of Done
Architecture alignment: Modules A -> B -> C -> D -> E -> F -> G -> H -> Outcome Feedback
Prepared as the continuation of the implemented ResolveAI foundation and Module D Document Intelligence design. Razorpay API integration notes were verified against current official documentation on 29 August 2026.
## 1. Position of Modules E-H in the ResolveAI Architecture
Figure 1. Post-document-intelligence pipeline: validate evidence deterministically, score risk statistically, draft only from grounded facts, and perform financial action only after authorised review.
### 1.1 End-to-End Sequence
1. Module D finishes document extraction and marks document-intelligence output ready for validation.
2. Module E resolves reason-code evidence requirements, validates completeness, links equivalent fields, detects contradictions and creates an immutable validation/feature snapshot.
3. Module F consumes only versioned feature snapshots, produces a calibrated contestability probability, computes cost-sensitive ACCEPT/REVIEW/CONTEST recommendation and stores explanations.
4. Module G retrieves applicable reason-code/policy guidance and generates a structured contest draft using only verified case facts and approved knowledge chunks.
5. A guardrail pass verifies that every factual claim is grounded in a case fact or retrieved policy source; unsupported claims are removed or force human review.
6. Module H places the case in a reviewer queue, displays evidence, findings, ML reasoning and generated draft, and records an explicit human action.
7. After approval, an idempotent external-action outbox uploads evidence documents if required and executes the Razorpay dispute draft/submit or accept action.
8. Outcome webhooks update the case to under_review/won/lost/closed and feed outcome labels back into evaluation and later retraining.
### 1.2 Non-Negotiable Architectural Boundaries

| Module | Owns | Must Not Own |
| --- | --- | --- |
| Module E | Deterministic evidence validation and feature preparation | Does not train/predict the final risk model. |
| Module F | Statistical risk scoring, calibration, thresholding and explanation | Does not generate contest text or call Razorpay action APIs. |
| Module G | Policy-grounded retrieval and response drafting | Does not decide the financial action and cannot invent evidence. |
| Module H | Human authorization and controlled external action | Does not silently override evidence/model outputs; all edits/actions are audited. |

## 2. Shared Contracts and Technical Foundations Across E-H
### 2.1 Reused Core Entities from Modules A-D
cases: the canonical case_id remains the primary cross-module business identifier.
disputes, payments, orders, shipments, refunds and customer_history: trusted enrichment facts from Modules A-B.
evidence_documents and evidence requirement metadata: secure files and intake status from Module C.
document_processing_jobs, document_extractions, extracted_fields, document_quality_assessments and document model versions: structured evidence from Module D.
audit_logs/shared error model/RBAC conventions: reused without creating parallel implementations.
### 2.2 Required Case State Progression
D_INTELLIGENCE_READY
-> E_VALIDATING
-> EVIDENCE_VALIDATED
-> FEATURE_READY
-> RISK_SCORING
-> RISK_SCORED
-> DECISION_READY
-> DRAFT_GENERATING
-> DRAFT_READY
-> REVIEW_PENDING
-> APPROVED_FOR_CONTEST | ACCEPT_APPROVED | MORE_EVIDENCE_REQUIRED
-> CONTEST_SUBMITTING
-> UNDER_REVIEW
-> WON | LOST | CLOSED
Rule: transitions must be validated server-side. A case cannot jump directly from D_INTELLIGENCE_READY to CONTEST_SUBMITTING.
### 2.3 Immutable Decision Snapshot
For every prediction/action, preserve the exact evidence and feature inputs used at that moment. Later edits or re-extractions must create a new snapshot rather than mutating historical decision inputs.

| Snapshot element | Purpose |
| --- | --- |
| evidence_version | Set of evidence document IDs + extraction versions used. |
| validation_run_id | Exact Module E rule execution and findings. |
| feature_snapshot_id | Immutable model input values. |
| model_version_id | Exact trained model/calibrator/threshold set. |
| policy_version_id | Reason-code evidence policy version. |
| prompt_template_version | Exact prompt used by Module G. |
| review_action_id | Human reviewer and final approved action. |

### 2.4 Shared Reliability Requirements
Idempotency at every asynchronous boundary: validation run, prediction, draft generation and external contest action.
Correlation IDs: case_id + request_id + run_id must be included in logs and queue messages.
UTC timestamps in backend/database; timezone conversion belongs to UI.
No raw secrets or unnecessary PII in logs, prompts or model features.
Version all rules, feature definitions, models, thresholds, knowledge sources and prompts.
Use an outbox pattern for any external financial/API action so database commit and API dispatch cannot diverge.
All automatic recommendations are advisory until the authorised human approval recorded in Module H.
Figure 2. Reproducible decision lineage.
## 3. Module E - Evidence Validation and Feature Preparation
### 3.1 Objective
Module E converts Module D extraction output into verified case-level evidence findings. It determines whether required evidence exists, whether extracted facts are internally and cross-source consistent, what is missing/unknown/contradictory, and which deterministic features are safe to send to the ML layer.
### 3.2 Inputs

| Input | Requirement | Use |
| --- | --- | --- |
| case_id | Required | Canonical case from Module A. |
| reason_code / reason_description / phase | Required | Determines applicable evidence-policy rules. |
| payment/order/shipment/refund facts | Required where available | Trusted structured sources from Module B. |
| evidence_documents | Required | Secure document metadata from Module C. |
| extracted_fields + provenance | Required | Field values/confidences/pages from Module D. |
| document quality scores | Recommended | Used to mark low-confidence/unknown evidence. |
| policy/rule version | Required | Ensures reproducibility of validation results. |

### 3.3 Detailed Technical Requirements

| ID | Requirement | Technical expectation |
| --- | --- | --- |
| E-01 | Eligibility gate | Process only cases whose Module D status is D_INTELLIGENCE_READY and whose required evidence documents are not quarantined/rejected. |
| E-02 | Reason-code rule resolution | Resolve the active evidence policy by payment network/reason code/phase and effective date. Unknown reason codes must route to REVIEW, not a guessed rule. |
| E-03 | Required vs optional evidence | Classify each expected evidence category as required/recommended/not-applicable and preserve the source policy version. |
| E-04 | Evidence presence assessment | Determine PRESENT / MISSING / UNKNOWN / UNUSABLE. Missing and unknown must never be collapsed into the same value. |
| E-05 | Cross-source entity linking | Link equivalent fields across payment/order/shipment/refund/documents (order_id, amount, customer, tracking_id, dates). Store source IDs for every comparison. |
| E-06 | Exact-match rules | Use exact comparison for stable identifiers after normalization: payment_id, order_id, tracking_id where trustworthy. |
| E-07 | Fuzzy-match rules | Use normalized similarity for names/addresses/descriptions; thresholds must be rule-versioned and low-confidence matches must abstain. |
| E-08 | Amount consistency | Compare disputed amount, captured amount, order/invoice amount, refunds and contestable net amount using currency-aware minor units and explicit tolerance rules. |
| E-09 | Refund consistency | Detect full/partial refund conflicts, refund-before-dispute patterns and claimed-no-refund contradictions. |
| E-10 | Timeline consistency | Validate allowed temporal order such as order -> payment -> dispatch -> delivery -> dispute, while supporting optional/missing events. |
| E-11 | Delivery/service evidence validation | For physical goods, validate tracking/delivery linkage; for digital/service cases, use service/access/activity evidence as applicable. |
| E-12 | Document quality propagation | If Module D extraction confidence/readability is below threshold, downgrade the finding to UNKNOWN/NEEDS_REVIEW instead of treating the value as false. |
| E-13 | Contradiction detection | Create machine-readable contradiction codes such as AMOUNT_MISMATCH, ORDER_ID_MISMATCH, DELIVERY_AFTER_DISPUTE, REFUND_CONFLICT. |
| E-14 | Evidence coverage metrics | Calculate required_evidence_coverage, recommended_evidence_coverage, verified_field_coverage and unknown_field_ratio. |
| E-15 | Consistency metrics | Calculate identifier_match_rate, amount_consistency_score, timeline_consistency_score, refund_consistency_score and document_quality_aggregate. |
| E-16 | Deterministic evidence score | Optionally compute a transparent validation score for UI/baseline comparison, but do not use it as the final ML probability. |
| E-17 | Feature generation | Produce only documented, versioned features. No free-form OCR text or future outcome fields should leak into the feature set unless explicitly designed. |
| E-18 | Leakage prevention | Exclude won/lost outcome, post-decision reviewer actions, submitted_at and any feature unavailable at prediction time. |
| E-19 | Explainable findings | Every failed/warned rule must include rule code, compared sources, normalized values, severity and reviewer-friendly explanation. |
| E-20 | Idempotent execution | Same case + evidence_version + rule_version returns the same validation_run or safely reuses it. |
| E-21 | Versioned rerun | New evidence, re-extraction or policy update creates a new run; historical runs remain immutable. |
| E-22 | Case-level readiness | Mark FEATURE_READY only when validation completed without fatal system errors. Business uncertainty is allowed and represented as features/flags. |

### 3.4 Core Validation Rules

| Rule code | Example logic | Severity | Purpose |
| --- | --- | --- | --- |
| PAYMENT_ORDER_LINK | payment.order_id == order.order_id | ERROR | Relationship integrity |
| INVOICE_AMOUNT_MATCH | invoice.amount ~= order/payment amount | WARN/ERROR | Tolerance-aware amount consistency |
| TRACKING_ORDER_LINK | tracking/order reference matches case order | ERROR | Shipment linkage |
| DELIVERY_BEFORE_DISPUTE | delivery_time <= dispute.created_at | ERROR | Temporal consistency |
| REFUND_AMOUNT_VALID | sum(refunds) <= captured amount | ERROR | Financial consistency |
| REQUIRED_EVIDENCE_PRESENT | reason-code required evidence is available and usable | ERROR | Policy completeness |
| OCR_CONFIDENCE_ACCEPTABLE | critical extracted field confidence >= configured threshold | WARN | Extraction reliability |

### 3.5 Module E Output Contract to Module F
{
"case_id": "CASE-00001",
"validation_run_id": "EVR-...",
"policy_version_id": "POL-...",
"feature_snapshot_id": "FS-...",
"findings": [ ...rule results... ],
"summary": {
"required_evidence_coverage": 0.75,
"identifier_match_rate": 1.0,
"timeline_consistency_score": 1.0,
"unknown_field_ratio": 0.12,
"contradiction_count": 1
},
"features": { ...versioned numeric/categorical inputs... },
"status": "FEATURE_READY"
}
### 3.6 Module E Database Design
Module E reuses cases, disputes, enrichment tables, evidence_documents and Module D extracted_fields. New tables below hold rules, validation executions, findings and model-ready snapshots.
#### Table E1 - evidence_validation_runs

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Validation run ID. |
| case_id | FK cases | Case being validated. |
| evidence_version | VARCHAR | Hash/version of evidence+extractions. |
| policy_version_id | FK evidence_policy_versions | Applied policy. |
| status | VARCHAR | RUNNING/COMPLETED/FAILED. |
| started_at | TIMESTAMPTZ | Start. |
| completed_at | TIMESTAMPTZ | Completion. |
| created_by | VARCHAR | worker/manual trigger. |
| idempotency_key | VARCHAR UNIQUE | Prevents duplicate run. |

#### Table E2 - validation_rule_catalog

| Column | Type / Key | Purpose |
| --- | --- | --- |
| rule_id | UUID / PK | Stable rule identity. |
| rule_code | VARCHAR UNIQUE | Human/machine rule code. |
| category | VARCHAR | IDENTITY/AMOUNT/TIME/REFUND/POLICY/QUALITY. |
| description | TEXT | Technical definition. |
| severity_default | VARCHAR | INFO/WARN/ERROR. |
| active | BOOLEAN | Rule enabled. |
| created_at | TIMESTAMPTZ | Audit timestamp. |

#### Table E3 - validation_rule_versions

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Version row. |
| rule_id | FK validation_rule_catalog | Parent rule. |
| version | INTEGER | Incrementing version. |
| parameters_json | JSONB | Thresholds/tolerances/normalization settings. |
| effective_from | TIMESTAMPTZ | Start of validity. |
| effective_to | TIMESTAMPTZ NULL | End of validity. |
| checksum | VARCHAR | Version integrity. |

#### Table E4 - evidence_validation_results

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Finding row. |
| validation_run_id | FK evidence_validation_runs | Run. |
| rule_version_id | FK validation_rule_versions | Rule executed. |
| result | VARCHAR | PASS/FAIL/WARN/UNKNOWN/NA. |
| severity | VARCHAR | Resolved severity. |
| source_refs | JSONB | Document/field/payment/order sources compared. |
| normalized_values | JSONB | Values used by rule. |
| explanation | TEXT | Reviewer-friendly result. |
| created_at | TIMESTAMPTZ | Timestamp. |

#### Table E5 - evidence_requirement_assessments

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Assessment. |
| validation_run_id | FK | Run. |
| evidence_type | VARCHAR | INVOICE/SHIPPING_PROOF/etc. |
| requirement_level | VARCHAR | REQUIRED/RECOMMENDED/NA. |
| status | VARCHAR | PRESENT/MISSING/UNKNOWN/UNUSABLE. |
| document_ids | JSONB | Evidence satisfying requirement. |
| coverage_weight | NUMERIC | Optional score weight. |
| reason | TEXT | Why status was assigned. |

#### Table E6 - cross_source_field_links

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Comparison/link. |
| validation_run_id | FK | Run. |
| semantic_field | VARCHAR | order_id/amount/customer_name/etc. |
| left_source | JSONB | Source A reference/value. |
| right_source | JSONB | Source B reference/value. |
| match_method | VARCHAR | EXACT/FUZZY/TOLERANCE. |
| match_score | NUMERIC | 0..1 where applicable. |
| link_status | VARCHAR | MATCH/MISMATCH/UNKNOWN. |

#### Table E7 - feature_definitions

| Column | Type / Key | Purpose |
| --- | --- | --- |
| feature_id | UUID / PK | Feature. |
| feature_name | VARCHAR UNIQUE | Stable model input name. |
| data_type | VARCHAR | NUMERIC/CATEGORICAL/BOOLEAN. |
| definition | TEXT | Exact calculation. |
| source_modules | VARCHAR | B/C/D/E. |
| version | INTEGER | Definition version. |
| available_at_prediction | BOOLEAN | Leakage safeguard. |
| active | BOOLEAN | Feature status. |

#### Table E8 - case_feature_snapshots

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Immutable feature snapshot. |
| case_id | FK cases | Case. |
| validation_run_id | FK | Run source. |
| feature_schema_version | VARCHAR | Feature contract version. |
| features_json | JSONB | Feature name/value map. |
| feature_hash | VARCHAR | Integrity/reuse key. |
| created_at | TIMESTAMPTZ | Snapshot time. |
| is_current | BOOLEAN | Latest eligible snapshot. |

### 3.7 Module E Acceptance Criteria

| Test | Expected result |
| --- | --- |
| All required evidence present and values match | No false contradiction; FEATURE_READY. |
| Invoice/payment amount mismatch | AMOUNT_MISMATCH finding with source references. |
| Low OCR confidence on amount | UNKNOWN/WARN, not hard mismatch. |
| Missing delivery proof when required | Requirement status MISSING. |
| Partial refund | Net amount calculation correct. |
| Same evidence/rule version rerun | Idempotent result. |
| New document added | New validation run and feature snapshot. |
| Outcome label present in source DB | Never included in prediction-time feature snapshot. |

## 4. Module F - Cost-Sensitive Risk ML and Decision Recommendation
### 4.1 Objective
Module F predicts the probability that the available evidence package is sufficiently strong and internally consistent to contest safely, then converts that probability into a cost-sensitive three-way recommendation. It must satisfy the hackathon requirement for honest held-out precision/recall and explicitly measure false-positive cost.
### 4.2 Target and Label Governance
Primary positive class: SAFE_TO_CONTEST / contestable evidence package under the defined gold-label policy.
Negative class: NOT_SAFE_TO_AUTOMATE / insufficient or materially contradictory evidence.
If real dispute outcome labels (won/lost) are later available, treat them as outcome targets for a separate model or later dataset version; do not silently equate won/lost with evidence sufficiency.
Synthetic/controlled labels must be clearly marked as synthetic and generated from documented case rules/scenarios.
Store label source, label method, reviewer/generator and label version.
### 4.3 Detailed Technical Requirements

| ID | Requirement | Technical expectation |
| --- | --- | --- |
| F-01 | Dataset builder | Construct training rows only from immutable feature snapshots and versioned labels. |
| F-02 | Leakage-safe split | Prefer temporal split; additionally group by merchant/customer/order/template when needed to prevent duplicated entities/documents across train/test. |
| F-03 | Three datasets | Maintain TRAIN / VALIDATION / HELD_OUT_TEST. Never tune thresholds/hyperparameters on held-out test. |
| F-04 | Baselines | Implement deterministic rules, Logistic Regression and at least one tree baseline before final CatBoost/LightGBM. |
| F-05 | Imbalance handling | Use class weights/cost-sensitive objectives first. Any resampling must occur only inside training folds. |
| F-06 | Feature preprocessing | Persist preprocessing transformers and categorical mappings with the model artifact; train-serving transformations must be identical. |
| F-07 | Model selection | Select by validation PR-AUC/F1 plus business-cost objective, not accuracy alone. |
| F-08 | Probability calibration | Calibrate winning model using Platt/sigmoid or isotonic on validation/calibration data; report Brier score/calibration curve. |
| F-09 | False-positive cost | Explicitly define FP as an unsafe case recommended for automated contest and assign higher cost than manual-review false negatives where appropriate. |
| F-10 | Threshold optimization | Optimize ACCEPT/REVIEW/CONTEST thresholds on validation data under expected cost and minimum precision/recall constraints. |
| F-11 | Abstention band | Use HUMAN_REVIEW for uncertain scores and for mandatory policy/quality flags regardless of model probability. |
| F-12 | Hard safety overrides | Fatal contradiction, missing mandatory evidence, expired response deadline or invalid case status must block auto-contest recommendation. |
| F-13 | Explainability | Store top feature contributions/SHAP values and deterministic findings separately; do not present SHAP as causal proof. |
| F-14 | Reproducibility | Record code commit, dependency lock, random seed, dataset version, feature schema, hyperparameters and environment. |
| F-15 | Metrics | Held-out: precision, recall, F1, PR-AUC, confusion matrix, false-positive rate/cost, false-negative cost and expected loss. |
| F-16 | Segment metrics | Report metrics by reason code, evidence quality band, amount band and optionally payment method to detect hidden failure modes. |
| F-17 | Robustness | Evaluate low-quality OCR, missing fields, added contradictions and partial evidence cases. |
| F-18 | Prediction idempotency | Same feature_snapshot_id + model_version_id produces one canonical prediction. |
| F-19 | Model registry | Only ACTIVE/APPROVED model versions can serve production/test predictions. |
| F-20 | Drift readiness | Store input distributions and prediction statistics so feature/prediction drift can be measured after deployment. |

### 4.4 Decision Logic
hard_block = mandatory_evidence_missing OR fatal_contradiction OR deadline_expired OR invalid_dispute_status
IF hard_block:
recommendation = REVIEW_OR_ACCEPT
ELSE IF calibrated_p >= T_contest:
recommendation = CONTEST
ELSE IF calibrated_p < T_accept:
recommendation = ACCEPT
ELSE:
recommendation = HUMAN_REVIEW
Thresholds T_accept and T_contest are learned/selected from validation data and stored as part of the model decision policy; they are not hard-coded only for the demo.
### 4.5 Cost Function
Recommended evaluation uses both classification metrics and an explicit business-loss function:
ExpectedCost = C_FP * FP + C_FN * FN + C_REVIEW * N_review
Optionally make cost amount-aware: ExpectedCaseLoss = P(incorrect action) x disputed_amount + operational review cost. Report both normalized and currency-based estimates when assumptions are transparent.
### 4.6 Module F Database Design
#### Table F1 - ml_datasets

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Dataset version. |
| name | VARCHAR | Dataset label. |
| label_definition | TEXT | Exact target definition. |
| feature_schema_version | VARCHAR | Input schema. |
| created_at | TIMESTAMPTZ | Created. |
| source_type | VARCHAR | SYNTHETIC/REAL/MIXED. |
| checksum | VARCHAR | Dataset manifest checksum. |
| notes | TEXT | Assumptions/limitations. |

#### Table F2 - ml_dataset_members

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Membership row. |
| dataset_id | FK ml_datasets | Dataset. |
| case_id | FK cases | Case. |
| feature_snapshot_id | FK case_feature_snapshots | Input snapshot. |
| label | VARCHAR/BOOLEAN | Target. |
| label_source | VARCHAR | RULE/REVIEW/OUTCOME/etc. |
| split | VARCHAR | TRAIN/VAL/TEST. |
| group_key | VARCHAR | Leakage control grouping. |

#### Table F3 - model_versions

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Model version. |
| model_name | VARCHAR | e.g., catboost_contestability. |
| algorithm | VARCHAR | LR/RF/CATBOOST/LIGHTGBM. |
| dataset_id | FK ml_datasets | Training dataset. |
| feature_schema_version | VARCHAR | Serving schema. |
| artifact_uri | TEXT | Model artifact location. |
| calibrator_uri | TEXT NULL | Calibration artifact. |
| code_commit | VARCHAR | Git commit. |
| status | VARCHAR | CANDIDATE/APPROVED/ACTIVE/RETIRED. |
| created_at | TIMESTAMPTZ | Version time. |

#### Table F4 - model_training_runs

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Training run. |
| model_version_id | FK model_versions | Model. |
| hyperparameters | JSONB | Training params. |
| random_seed | INTEGER | Reproducibility. |
| environment_json | JSONB | Package/GPU/CPU info. |
| started_at | TIMESTAMPTZ | Start. |
| completed_at | TIMESTAMPTZ | Finish. |
| status | VARCHAR | Run status. |

#### Table F5 - model_metrics

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Metric row. |
| model_version_id | FK | Model. |
| split | VARCHAR | VAL/TEST. |
| segment_key | VARCHAR NULL | ALL/reason_code/etc. |
| metric_name | VARCHAR | precision/recall/f1/pr_auc/brier/fp_cost. |
| metric_value | NUMERIC | Value. |
| metric_context | JSONB | Threshold/cost assumptions. |

#### Table F6 - model_decision_policies

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Policy. |
| model_version_id | FK | Model. |
| version | INTEGER | Decision version. |
| accept_threshold | NUMERIC | Lower threshold. |
| contest_threshold | NUMERIC | Upper threshold. |
| fp_cost | NUMERIC | FP cost assumption. |
| fn_cost | NUMERIC | FN cost assumption. |
| review_cost | NUMERIC | Review cost. |
| hard_block_rules | JSONB | Safety overrides. |
| active | BOOLEAN | Serving policy. |

#### Table F7 - risk_predictions

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Prediction. |
| case_id | FK cases | Case. |
| feature_snapshot_id | FK | Exact input. |
| model_version_id | FK | Model. |
| decision_policy_id | FK | Threshold policy. |
| raw_score | NUMERIC | Uncalibrated score. |
| calibrated_probability | NUMERIC | 0..1 calibrated. |
| recommendation | VARCHAR | ACCEPT/REVIEW/CONTEST. |
| hard_block | BOOLEAN | Safety override. |
| created_at | TIMESTAMPTZ | Prediction time. |
| idempotency_key | VARCHAR UNIQUE | Duplicate prevention. |

#### Table F8 - prediction_explanations

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Explanation row. |
| prediction_id | FK risk_predictions | Prediction. |
| feature_name | VARCHAR | Feature. |
| feature_value | JSONB | Value. |
| contribution | NUMERIC | SHAP/contribution. |
| rank | INTEGER | Display order. |
| direction | VARCHAR | SUPPORTS/OPPOSES contest. |
| display_text | TEXT | Safe explanation. |

### 4.7 Module F Acceptance Criteria

| Test | Expected result |
| --- | --- |
| Held-out test not used in tuning | Test metrics generated once after model/threshold freeze. |
| Class imbalance | PR-AUC and cost-sensitive results shown; accuracy is not headline metric. |
| High model score but missing mandatory evidence | Hard block prevents CONTEST. |
| Same snapshot/model rerun | Same canonical prediction/idempotent result. |
| Probability calibration | Brier/calibration metric stored. |
| Explainability | Top contributions stored and linked to exact prediction. |
| Robustness scenario | Performance degradation is measured and reported, not hidden. |

## 5. Module G - Policy-Grounded RAG and LLM Auto-Responder
### 5.1 Objective
Module G creates a concise dispute contest draft and evidence summary after Module F, using retrieved reason-code guidance plus verified case facts. The LLM is a controlled language-generation component, not the source of truth and not the financial decision maker.
### 5.2 Knowledge Sources
Razorpay dispute reason-code/evidence guidance and API submission requirements.
Merchant-approved terms, cancellation/refund policy and service/delivery policy where relevant.
Internal response templates reviewed for tone and legal/operational suitability.
Only sources with owner, version, effective dates and trust level may enter the production RAG index.
### 5.3 Detailed Technical Requirements

| ID | Requirement | Technical expectation |
| --- | --- | --- |
| G-01 | Knowledge ingestion | Load approved sources with source_id, title, URL/file reference, effective dates, network/reason-code metadata and checksum. |
| G-02 | Chunking | Chunk by semantic section/headings; preserve section title and source pointer. Avoid arbitrary tiny chunks that lose policy context. |
| G-03 | Embeddings | Store vector embeddings in pgvector (sufficient for hackathon) with embedding model/version metadata. |
| G-04 | Metadata filtering | Retrieve using reason_code, payment network, phase, evidence type, effective date and source trust level before semantic ranking. |
| G-05 | Query construction | Build retrieval query from reason code, validation findings and intended action; do not send raw sensitive document text when structured facts suffice. |
| G-06 | Retrieval quality | Persist top-k candidates, similarity scores and final selected chunks for later evaluation/audit. |
| G-07 | Fact allowlist | LLM case facts must be assembled from Module B trusted facts + Module E verified fields + Module F recommendation. Unsupported OCR text is excluded unless explicitly marked uncertain. |
| G-08 | Structured prompt | Use system/instruction template + policy context + case facts + output JSON schema. Prompts must be versioned. |
| G-09 | Structured output | Return summary, contest_amount suggestion, evidence list, claim list and final draft; validate JSON with Pydantic/schema. |
| G-10 | No hallucinated evidence | The model may reference only document IDs/evidence types present in the provided fact packet. |
| G-11 | Claim-level grounding | Extract each factual claim and link it to one or more case facts or retrieved knowledge chunks. |
| G-12 | Citation coverage | Require all material policy claims and case assertions to have evidence/source links. Low coverage routes to human review. |
| G-13 | Contradiction guardrail | Compare generated dates, amounts, IDs and status terms against the structured fact packet; mismatch invalidates draft. |
| G-14 | Sensitive-data minimization | Redact/hash unnecessary PII before prompt logging or third-party LLM calls. |
| G-15 | Prompt-injection resistance | Treat evidence document text and retrieved content as data, not instructions; keep fixed system policy and strip/ignore embedded instructions. |
| G-16 | Model fallback | If LLM unavailable, generate a deterministic template draft from verified fields; the workflow must remain functional. |
| G-17 | Length control | Respect downstream summary/action length constraints; Razorpay contest summary is currently documented with a maximum length of 1000 characters. |
| G-18 | Draft only by default | Generate/save draft first. Submission must be performed only by Module H after approval. |
| G-19 | RAG evaluation | Measure retrieval hit rate/precision@k on a small gold set, plus draft groundedness, claim support rate, contradiction rate and human edit rate. |
| G-20 | Version lineage | Store knowledge-index version, embedding model, LLM model, prompt template and guardrail version with every generated draft. |

### 5.4 Recommended Generated Output Schema
{
"case_id": "CASE-00001",
"prediction_id": "PRED-...",
"recommended_action": "CONTEST",
"contest_amount_minor": 1249900,
"evidence_document_ids": ["DOC-1", "DOC-2"],
"summary": "... <= downstream limit ...",
"claims": [
{"claim": "Order was delivered on ...", "fact_refs": ["shipment.delivery_time"], "source_refs": ["DOC-2:p1"]}
],
"missing_or_uncertain": [],
"guardrail_status": "PASS"
}
### 5.5 Module G Database Design
#### Table G1 - knowledge_sources

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Knowledge source. |
| source_type | VARCHAR | RAZORPAY_DOC/MERCHANT_POLICY/TEMPLATE. |
| title | TEXT | Display title. |
| source_uri | TEXT | URL/object location. |
| trust_level | VARCHAR | OFFICIAL/APPROVED/INTERNAL. |
| effective_from | TIMESTAMPTZ | Validity. |
| effective_to | TIMESTAMPTZ NULL | Expiry. |
| checksum | VARCHAR | Source integrity. |
| active | BOOLEAN | Index eligibility. |

#### Table G2 - knowledge_chunks

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Chunk. |
| source_id | FK knowledge_sources | Parent. |
| chunk_index | INTEGER | Order. |
| content | TEXT | Chunk text. |
| metadata_json | JSONB | Reason code/network/section/evidence type. |
| embedding | VECTOR | pgvector embedding. |
| embedding_model_version | VARCHAR | Embedding lineage. |
| content_hash | VARCHAR | Dedup/version integrity. |

#### Table G3 - rag_retrieval_runs

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Retrieval run. |
| case_id | FK cases | Case. |
| prediction_id | FK risk_predictions | Risk context. |
| query_text_redacted | TEXT | Safe query. |
| filters_json | JSONB | Reason/network/date filters. |
| top_k | INTEGER | Requested k. |
| index_version | VARCHAR | Knowledge index. |
| created_at | TIMESTAMPTZ | Run time. |

#### Table G4 - rag_retrieved_chunks

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Result row. |
| retrieval_run_id | FK | Run. |
| chunk_id | FK knowledge_chunks | Chunk. |
| rank | INTEGER | Rank. |
| similarity_score | NUMERIC | Vector score. |
| selected_for_prompt | BOOLEAN | Actually used. |
| selection_reason | TEXT | Optional rerank rationale. |

#### Table G5 - prompt_templates

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Prompt template. |
| name | VARCHAR | contest_response. |
| version | INTEGER | Template version. |
| system_template | TEXT | Fixed policy/instructions. |
| user_template | TEXT | Case/context template. |
| output_schema | JSONB | Required structured output. |
| active | BOOLEAN | Serving template. |
| checksum | VARCHAR | Integrity. |

#### Table G6 - llm_model_versions

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | LLM config. |
| provider | VARCHAR | Provider/local. |
| model_name | VARCHAR | Model. |
| parameters_json | JSONB | Temperature/max tokens/etc. |
| data_policy | TEXT | PII/retention constraints. |
| active | BOOLEAN | Serving enabled. |
| created_at | TIMESTAMPTZ | Version time. |

#### Table G7 - response_generation_runs

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Generation run. |
| case_id | FK cases | Case. |
| prediction_id | FK risk_predictions | Prediction. |
| retrieval_run_id | FK | Retrieved context. |
| prompt_template_id | FK | Prompt version. |
| llm_model_version_id | FK | Model. |
| fact_packet_hash | VARCHAR | Exact case facts. |
| status | VARCHAR | RUNNING/PASS/FAILED. |
| started_at | TIMESTAMPTZ | Start. |
| completed_at | TIMESTAMPTZ | Finish. |

#### Table G8 - generated_drafts

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Draft. |
| generation_run_id | FK | Run. |
| case_id | FK | Case. |
| summary | TEXT | Generated contest summary. |
| contest_amount_minor | BIGINT | Suggested amount. |
| draft_json | JSONB | Structured output. |
| guardrail_status | VARCHAR | PASS/REVIEW/FAIL. |
| created_at | TIMESTAMPTZ | Time. |
| is_current | BOOLEAN | Latest draft. |

#### Table G9 - draft_claims

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Claim. |
| draft_id | FK generated_drafts | Draft. |
| claim_text | TEXT | Atomic factual/policy claim. |
| claim_type | VARCHAR | CASE_FACT/POLICY/RECOMMENDATION. |
| support_status | VARCHAR | SUPPORTED/UNSUPPORTED/CONFLICT. |
| fact_refs | JSONB | Structured case references. |
| chunk_refs | JSONB | RAG source chunk references. |

#### Table G10 - llm_guardrail_results

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Guardrail finding. |
| draft_id | FK generated_drafts | Draft. |
| check_type | VARCHAR | SCHEMA/GROUNDING/CONTRADICTION/PII/PROMPT_INJECTION. |
| result | VARCHAR | PASS/WARN/FAIL. |
| details_json | JSONB | Machine-readable details. |
| created_at | TIMESTAMPTZ | Time. |

### 5.6 Module G Acceptance Criteria

| Test | Expected result |
| --- | --- |
| Reason-code guidance retrieved | Selected chunks match required evidence category and version. |
| Unsupported generated delivery date | Guardrail marks CONFLICT/FAIL. |
| Model names a document not in fact packet | Draft rejected. |
| RAG/LLM unavailable | Deterministic fallback draft produced. |
| PII not required for response | Not sent/logged in prompt. |
| Prompt injection text inside evidence | Ignored as untrusted data. |
| Every material claim | Has fact/source reference or routes to review. |

## 6. Module H - Human Review, Approval and Razorpay Contest/Accept Action
### 6.1 Objective
Module H is the control point where a qualified user reviews the evidence package, validation findings, ML recommendation and generated draft, then explicitly approves the financial/dispute action. It is also responsible for safe integration with Razorpay dispute/document APIs and outcome feedback.
### 6.2 Current Razorpay Action Mapping
Current official Razorpay documentation exposes a dispute contest endpoint PATCH /v1/disputes/:id/contest. Evidence documents can be uploaded via POST /v1/documents with purpose=dispute_evidence. The contest endpoint supports draft and submit actions; submission requires at least one document ID, and successful submission moves an open dispute to under_review. Evidence attributes include shipping_proof, billing_proof, cancellation_proof, customer_communication, proof_of_service, explanation_letter, refund_confirmation, access_activity_log, refund_cancellation_policy, term_and_conditions and others.
### 6.3 Detailed Technical Requirements

| ID | Requirement | Technical expectation |
| --- | --- | --- |
| H-01 | Reviewer queue | Prioritize by respond_by deadline, disputed amount, recommendation, hard-block flags and evidence readiness. |
| H-02 | Case review screen | Display immutable evidence snapshot, document preview, Module E findings, Module F probability/SHAP, Module G draft and all uncertainty warnings. |
| H-03 | Permitted reviewer actions | APPROVE_CONTEST, APPROVE_ACCEPT, REQUEST_MORE_EVIDENCE, EDIT_DRAFT, REJECT_RECOMMENDATION, ESCALATE. |
| H-04 | RBAC | Only authorised merchant/risk roles can approve financial action; read-only roles cannot submit. |
| H-05 | Dual control | Optionally require second approver for high disputed amounts or risky/hard-block override cases. |
| H-06 | Deadline recheck | Immediately before external action, re-fetch/revalidate dispute status and respond_by. Expired or non-actionable disputes must be blocked. |
| H-07 | Contest amount validation | Contest amount must be positive and must not exceed dispute amount; handle full vs partial contest explicitly. |
| H-08 | Evidence mapping | Map internal evidence types to Razorpay evidence fields and upload only approved safe documents. |
| H-09 | Razorpay document upload | Upload required local evidence to /v1/documents with purpose=dispute_evidence and persist returned document IDs. |
| H-10 | Draft before submit | Support action=draft for safe preview/testing; only approved submission uses action=submit. |
| H-11 | At least one evidence document | Block submit if no valid Razorpay document ID is mapped to the contest request. |
| H-12 | Summary validation | Enforce contest summary length/content constraints and remove unsupported claims before API call. |
| H-13 | External action outbox | Write approved action to DB transactionally, then dispatch asynchronously. Never directly call external API before approval commit. |
| H-14 | Idempotency/dedup | One approved action package has one idempotency/outbox key. Retry transport failures without creating duplicate logical submissions. |
| H-15 | Attempt history | Persist every external call attempt, HTTP result, safe response metadata and retry decision. |
| H-16 | State reconciliation | On success, update local state from Razorpay response; later webhooks remain authoritative for under_review/won/lost/closed transitions. |
| H-17 | Accept path | If reviewer approves ACCEPT, invoke the configured dispute-accept integration only after the same authorization/deadline/status checks. |
| H-18 | Manual override reason | If reviewer contradicts ML recommendation or overrides a hard block, require reason code and free-text justification. |
| H-19 | Outcome feedback | Store won/lost/closed result and link it to prediction, draft, reviewer action and submitted evidence package. |
| H-20 | Training feedback safety | Outcome labels enter future ML datasets only through an explicit label curation/versioning step; never automatically retrain from raw webhook outcomes. |
| H-21 | Test/sandbox mode | Default hackathon environment to simulated or Razorpay test mode; clearly distinguish DRY_RUN/DRAFT/SUBMIT in UI and audit logs. |
| H-22 | Observability | Track queue age, cases near deadline, submission success rate, API errors, review turnaround and won/lost outcome metrics. |

### 6.4 Human Review Decision Matrix

| System state | Reviewer choices | Action rule |
| --- | --- | --- |
| CONTEST + no hard blocks + guardrail PASS | Approve contest / edit draft / request evidence | Eligible for submit after approval. |
| REVIEW recommendation | Request evidence / approve contest with justification / accept | Human judgment required. |
| ACCEPT recommendation | Approve accept / override to contest with justification | No auto-submit. |
| Hard block present | Request evidence / accept / explicit escalated override | Default contest blocked. |
| Deadline expired / dispute non-actionable | No contest action | Close/escalate only. |

### 6.5 Module H Database Design
#### Table H1 - review_queue_items

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Queue item. |
| case_id | FK cases | Case. |
| prediction_id | FK risk_predictions | Current risk context. |
| draft_id | FK generated_drafts NULL | Current draft. |
| priority_score | NUMERIC | Deadline/amount/risk priority. |
| queue_status | VARCHAR | PENDING/ASSIGNED/DONE. |
| assigned_to | FK users NULL | Reviewer. |
| respond_by | TIMESTAMPTZ | Deadline. |
| created_at | TIMESTAMPTZ | Queue time. |

#### Table H2 - review_actions

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Review action. |
| queue_item_id | FK | Queue item. |
| case_id | FK cases | Case. |
| reviewer_id | FK users | Actor. |
| action | VARCHAR | APPROVE_CONTEST/ACCEPT/REQUEST_MORE/etc. |
| override_reason_code | VARCHAR NULL | Required for override. |
| notes | TEXT | Reviewer rationale. |
| draft_revision_json | JSONB NULL | Approved edits. |
| created_at | TIMESTAMPTZ | Action time. |

#### Table H3 - contest_packages

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Immutable approved package. |
| case_id | FK cases | Case. |
| review_action_id | FK review_actions | Approval. |
| draft_id | FK generated_drafts | Approved draft. |
| contest_amount_minor | BIGINT | Amount. |
| summary | TEXT | Approved summary. |
| package_hash | VARCHAR | Integrity. |
| status | VARCHAR | DRAFT/APPROVED/SUBMITTED/FAILED. |
| created_at | TIMESTAMPTZ | Package time. |

#### Table H4 - contest_package_documents

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Package document. |
| contest_package_id | FK | Package. |
| document_id | FK evidence_documents | Internal evidence. |
| razorpay_evidence_field | VARCHAR | shipping_proof/billing_proof/etc. |
| approved | BOOLEAN | Reviewer approval. |
| sort_order | INTEGER | Stable ordering. |

#### Table H5 - razorpay_document_links

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | External document mapping. |
| document_id | FK evidence_documents | Local evidence. |
| razorpay_document_id | VARCHAR UNIQUE | Returned doc_* ID. |
| purpose | VARCHAR | dispute_evidence. |
| mime_type | VARCHAR | Uploaded type. |
| size_bytes | BIGINT | Uploaded size. |
| uploaded_at | TIMESTAMPTZ | Upload time. |
| external_response_json | JSONB | Sanitized response. |

#### Table H6 - external_action_outbox

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Outbox event. |
| case_id | FK cases | Case. |
| action_type | VARCHAR | UPLOAD_DOCUMENT/CONTEST_DRAFT/CONTEST_SUBMIT/ACCEPT. |
| aggregate_id | UUID | Package/document/action ID. |
| payload_json | JSONB | Prepared safe external request. |
| idempotency_key | VARCHAR UNIQUE | Logical action key. |
| status | VARCHAR | PENDING/PROCESSING/SENT/FAILED. |
| attempt_count | INTEGER | Retry counter. |
| next_attempt_at | TIMESTAMPTZ | Backoff. |
| created_at | TIMESTAMPTZ | Committed time. |

#### Table H7 - external_action_attempts

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Attempt. |
| outbox_id | FK external_action_outbox | Action. |
| attempt_no | INTEGER | Attempt sequence. |
| request_metadata | JSONB | No secrets; endpoint/method/hash. |
| http_status | INTEGER NULL | Response status. |
| response_metadata | JSONB NULL | Sanitized response. |
| error_code | VARCHAR NULL | Mapped error. |
| started_at | TIMESTAMPTZ | Start. |
| completed_at | TIMESTAMPTZ | Finish. |

#### Table H8 - contest_submissions

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Submission record. |
| contest_package_id | FK contest_packages | Package. |
| external_dispute_id | VARCHAR | Razorpay dispute id. |
| action | VARCHAR | draft/submit. |
| external_status | VARCHAR | open/under_review/etc. |
| submitted_at | TIMESTAMPTZ NULL | External submit time. |
| razorpay_evidence_json | JSONB | Mapped external evidence IDs. |
| response_snapshot | JSONB | Sanitized response. |
| status | VARCHAR | SUCCESS/FAILED/PENDING. |

#### Table H9 - dispute_outcomes

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Outcome. |
| case_id | FK cases | Case. |
| prediction_id | FK risk_predictions NULL | Prediction lineage. |
| contest_submission_id | FK contest_submissions NULL | Submission. |
| outcome | VARCHAR | WON/LOST/CLOSED/UNDER_REVIEW. |
| amount_deducted_minor | BIGINT NULL | If available. |
| source_event_id | VARCHAR | Webhook/event source. |
| occurred_at | TIMESTAMPTZ | Outcome time. |
| created_at | TIMESTAMPTZ | Stored time. |

#### Table H10 - curated_feedback_labels

| Column | Type / Key | Purpose |
| --- | --- | --- |
| id | UUID / PK | Curated label. |
| case_id | FK cases | Case. |
| outcome_id | FK dispute_outcomes NULL | Outcome source. |
| label_name | VARCHAR | e.g., contest_outcome. |
| label_value | VARCHAR | won/lost/etc. |
| label_quality | VARCHAR | GOLD/SILVER/SYNTHETIC. |
| curated_by | FK users/system | Reviewer/process. |
| version | INTEGER | Label version. |
| approved_for_training | BOOLEAN | Dataset eligibility. |
| created_at | TIMESTAMPTZ | Time. |

### 6.6 Module H Acceptance Criteria

| Test | Expected result |
| --- | --- |
| Reviewer lacks submit permission | 403; no outbox action. |
| Contest amount > dispute amount | Blocked before API call. |
| No evidence document IDs | Submit blocked. |
| Approved package retried | One logical outbox action; attempts are recorded. |
| Razorpay API timeout | Retry with backoff; no duplicate review action. |
| Status changed to under_review before retry | Reconciliation prevents invalid resubmission. |
| Reviewer overrides model | Mandatory reason and audit record. |
| Won/lost webhook received | Outcome linked to exact prediction/package/reviewer action. |

## 7. Cross-Module Database Relationships
cases
|--< evidence_validation_runs --< evidence_validation_results
| --< evidence_requirement_assessments
| --< cross_source_field_links
|--< case_feature_snapshots --< risk_predictions --< prediction_explanations
| |
| +--< rag_retrieval_runs --< rag_retrieved_chunks
| +--< response_generation_runs --< generated_drafts --< draft_claims
| --< llm_guardrail_results
|--< review_queue_items --< review_actions --< contest_packages --< contest_package_documents
| |
| +--< contest_submissions --< dispute_outcomes
|--< external_action_outbox --< external_action_attempts
|--< curated_feedback_labels
knowledge_sources --< knowledge_chunks
model_versions --< model_training_runs / model_metrics / model_decision_policies
### 7.1 Critical Integrity Constraints
risk_predictions(feature_snapshot_id, model_version_id) should have a uniqueness/idempotency constraint for canonical serving predictions.
case_feature_snapshots are immutable after creation; create a new snapshot when evidence or rule output changes.
contest_packages are immutable after APPROVED; edits create a new package/review action.
razorpay_document_links must uniquely map one successfully uploaded external document ID.
external_action_outbox.idempotency_key is UNIQUE and payload is hashable/auditable.
Only one active model decision policy per serving model/environment.
Only active knowledge source versions are eligible for retrieval by effective date.
Outcome events are deduplicated using source_event_id and case/dispute identity.
### 7.2 Recommended Indexes

| Table | Recommended indexes |
| --- | --- |
| evidence_validation_runs | (case_id, created_at DESC); idempotency_key UNIQUE |
| evidence_validation_results | (validation_run_id, severity); (validation_run_id, result) |
| case_feature_snapshots | (case_id, is_current); feature_hash |
| risk_predictions | (case_id, created_at DESC); idempotency_key UNIQUE |
| knowledge_chunks | HNSW/IVFFlat vector index + metadata indexes on source/reason/network |
| review_queue_items | (queue_status, priority_score DESC, respond_by ASC) |
| external_action_outbox | (status, next_attempt_at); idempotency_key UNIQUE |
| dispute_outcomes | (case_id, occurred_at DESC); source_event_id UNIQUE |

## 8. API and Queue Contracts

| Endpoint | Owner | Purpose |
| --- | --- | --- |
| POST /api/v1/cases/{id}/validate-evidence | E | Start/reuse validation run. |
| GET /api/v1/cases/{id}/validation | E | Validation summary/findings. |
| POST /api/v1/cases/{id}/risk-score | F | Create/reuse prediction. |
| GET /api/v1/cases/{id}/risk | F | Probability, recommendation, explanation. |
| POST /api/v1/cases/{id}/generate-draft | G | Run retrieval + grounded draft generation. |
| GET /api/v1/cases/{id}/draft | G | Current draft + grounding status. |
| POST /api/v1/reviews/{queue_id}/action | H | Reviewer action. |
| POST /api/v1/cases/{id}/contest-package | H | Freeze approved package. |
| POST /api/v1/cases/{id}/submit-contest | H | Enqueue approved external action; never direct unauthorised call. |
| GET /api/v1/cases/{id}/timeline | Shared | Full audit/decision timeline. |

### 8.1 Recommended Queue Events
DOCUMENT_INTELLIGENCE_READY -> VALIDATE_EVIDENCE
EVIDENCE_VALIDATED -> BUILD_FEATURE_SNAPSHOT
FEATURE_READY -> SCORE_RISK
RISK_SCORED -> GENERATE_DRAFT (when applicable)
DRAFT_READY -> ENQUEUE_REVIEW
CONTEST_APPROVED -> PREPARE_CONTEST_PACKAGE
CONTEST_PACKAGE_APPROVED -> DISPATCH_EXTERNAL_ACTION
OUTCOME_WEBHOOK -> RECORD_OUTCOME / CURATE_FEEDBACK
## 9. Shared Error Model for E-H

| Error code | Module | Handling |
| --- | --- | --- |
| VALIDATION_POLICY_NOT_FOUND | E | Non-retryable business/config error; route to review. |
| VALIDATION_SOURCE_UNCERTAIN | E | Business uncertainty; continue with UNKNOWN flag. |
| FEATURE_SCHEMA_MISMATCH | F | Fatal serving error; do not predict. |
| MODEL_NOT_ACTIVE | F | Configuration error. |
| RAG_NO_RELEVANT_CONTEXT | G | Fallback template/review. |
| LLM_SCHEMA_INVALID | G | Retry once with constrained repair or fallback. |
| LLM_GROUNDING_FAILED | G | Do not present as approved draft. |
| REVIEW_PERMISSION_DENIED | H | 403. |
| CONTEST_DEADLINE_EXPIRED | H | Do not call external API. |
| CONTEST_INVALID_STATUS | H | Reconcile and block. |
| EXTERNAL_API_RETRYABLE | H | Outbox retry with backoff. |
| EXTERNAL_API_NONRETRYABLE | H | Fail action; show safe error to reviewer. |

## 10. Security, Privacy and Governance Additions
Use least-privilege service accounts for ML/RAG workers and external action workers.
Separate encrypted object storage for evidence from vector/feature databases; do not embed whole sensitive documents if structured/policy text is enough.
Encrypt secrets and API credentials outside source code; rotate keys and prevent them from entering logs.
Store prompt/response logs only after PII minimization and according to the selected model provider data policy.
Apply merchant tenancy checks to every case/evidence/review endpoint, not only the UI.
Audit model/prompt/policy activation changes as privileged administrative actions.
Introduce retention/deletion rules for evidence and derived embeddings consistent with project policy.
Keep human approval as the default for contest submission during the hackathon; do not market an uncontrolled financial auto-action.
## 11. Testing Strategy Across E-H

| Test layer | Minimum coverage |
| --- | --- |
| Unit | Rule normalization, comparisons, feature functions, threshold logic, prompt schema, evidence mapping. |
| Integration | D->E extraction handoff; E->F snapshot/prediction; F->G fact packet; G->H approved package. |
| Security | Tenant isolation, RBAC, prompt injection, file/document authorization, secret/log checks. |
| ML | Leakage test, split reproducibility, calibration, segment metrics, robustness, held-out evaluation. |
| RAG | Gold query set, retrieval hit rate, wrong-reason-code filtering, stale-source exclusion. |
| LLM | Schema validation, unsupported claim rejection, contradiction checks, fallback path. |
| External API | Mock timeout/400/401/429/5xx, retry logic, non-actionable status, deadline expiry. |
| End-to-end | At least 5 deterministic cases covering CONTEST, REVIEW, ACCEPT, missing evidence and mismatch/low-quality evidence. |

## 12. Recommended Implementation Order
1. Freeze Module E rule/policy schema and output feature contract.
2. Implement Module E validation engine with 8-12 high-value deterministic rules and tests.
3. Generate/rebuild the synthetic benchmark using those exact canonical features and labels.
4. Implement F baselines and held-out evaluation pipeline before the final model.
5. Train CatBoost/LightGBM, calibrate probability and freeze cost-sensitive thresholds.
6. Implement risk API, prediction lineage and SHAP/explanation storage.
7. Build a small official reason-code/evidence knowledge corpus in pgvector.
8. Implement RAG retrieval + fact allowlist + structured draft generation + guardrails.
9. Implement reviewer queue and approved contest package; keep human approval mandatory.
10. Implement Razorpay document/contest integration behind an external-action outbox in test/dry-run mode first.
11. Add outcome webhook reconciliation and curated feedback label tables.
12. Run end-to-end demo cases, robustness tests and freeze final held-out metrics.
### 12.1 Hackathon Priority: Must Have vs Strong Additions

| Priority | Deliverable |
| --- | --- |
| MUST | E: reason-code completeness + amount/order/timeline/refund checks + feature snapshot |
| MUST | F: rules + Logistic Regression + CatBoost/LightGBM + calibration + held-out metrics + FP cost |
| MUST | F/H: three-way recommendation + hard safety blocks + human approval |
| MUST | G: grounded structured draft with at least basic reason-code retrieval and hallucination checks |
| MUST | H: safe contest package + dry-run/test-mode external action + audit |
| STRONG | SHAP, segment metrics and robustness evaluation |
| STRONG | Claim-level source links and RAG evaluation set |
| STRONG | Outbox retries/reconciliation and won/lost feedback loop |
| OPTIONAL | Automated retraining/drift alert UI; keep design-ready if time is limited |

## 13. End-to-End Demo Scenarios

| Scenario | Input condition | Expected flow |
| --- | --- | --- |
| Case 1 - Strong contest | Invoice/payment/order match; delivery proof valid; no refund; high-quality evidence | E passes -> F CONTEST -> G grounded draft -> H approve/draft/submit. |
| Case 2 - Missing evidence | Reason code requires delivery/service proof but document missing | E MISSING -> F hard block/REVIEW -> H request evidence. |
| Case 3 - Contradiction | Invoice amount differs materially from captured/order amount | E AMOUNT_MISMATCH -> F REVIEW/ACCEPT -> no auto contest. |
| Case 4 - Low OCR quality | Critical amount/date unreadable | E UNKNOWN rather than mismatch -> F abstains -> H review. |
| Case 5 - Refunded case | Full refund before dispute | E refund conflict/covered -> F ACCEPT or review -> H no contest unless justified. |

## 14. Definition of Done
Module E produces versioned, explainable validation findings and immutable model feature snapshots from Module D output.
Module F produces reproducible held-out precision/recall/F1/PR-AUC, calibrated probabilities and explicit false-positive cost with frozen thresholds.
Module G produces a structured contest draft that contains no unsupported evidence claims and records retrieval/prompt/model lineage.
Module H requires authorised human action, freezes an immutable contest package and uses an idempotent outbox for external API actions.
All modules preserve case_id, tenant isolation, audit logs, version lineage, errors and state-machine consistency established in Modules A-D.
At least five end-to-end demo cases execute deterministically and the final presentation distinguishes synthetic benchmark results from real production outcomes.
## 15. Current Razorpay Integration Notes Used in This Design

| Integration point | Design implication |
| --- | --- |
| Contest endpoint | PATCH /v1/disputes/:id/contest; supports draft or submit action. |
| Submission evidence | At least one document ID is required for submit; evidence can be associated through typed fields. |
| Contest amount | Can be partial/full; cannot exceed dispute amount. |
| Contest summary | Current API documentation states a maximum length of 1000 characters. |
| Document upload | POST /v1/documents with purpose=dispute_evidence; returned doc_* ID is used in dispute evidence. |
| Status after submit | Submission changes an open dispute to under_review and triggers payment.dispute.under_review. |
| Outcome events | Dispute webhooks include created, action_required, under_review, won, lost and closed states/events documented by Razorpay. |

## 16. References
Razorpay API Reference - Contest a Dispute: https://razorpay.com/docs/api/disputes/contest/?preferred-country=IN
Razorpay API Reference - Disputes APIs: https://razorpay.com/docs/api/disputes/
Razorpay API Reference - Create a Document: https://razorpay.com/docs/api/documents/create/?preferred-country=IN
Razorpay Docs - Submit Evidence: https://razorpay.com/docs/payments/disputes/submit-evidence/?preferred-country=IN
Razorpay Docs - Disputes Webhook Events: https://razorpay.com/docs/webhooks/disputes/?preferred-country=IN
Project architecture basis: ResolveAI Modules A-B-C Requirements & Database Design and Module D Document Intelligence Requirements & Database Design.
