# ResolveAI | Module F — Step-by-Step ML Implementation Plan

**Razorpay AI Risk Manager Hackathon — Technical Implementation Blueprint**

## Cost-Sensitive Evidence Contestability Model

**CatBoost / LightGBM / Calibration / SHAP / Held-out Evaluation**

### Architecture position

Module E has already produced validated evidence, contradiction flags and canonical feature candidates. Module F converts those deterministic outputs into a reproducible ML feature snapshot, trains and evaluates contestability models, calibrates probabilities, applies false-positive-cost-aware thresholds, and hands a controlled **ACCEPT / HUMAN_REVIEW / CONTEST** recommendation to Module G.

**Prepared for implementation after successful completion of Modules A-E.**

### Embedded diagram — Page 1

```text
Module E
Feature Snapshot
      ↓
Synthetic
Benchmark
      ↓
Freeze
Train/Val/Test
      ↓
Baselines
      ↓
CatBoost +
LightGBM
      ↓
Calibration
      ↓
Cost-based
Policy
      ↓
SHAP +
Hold-out Eval
      ↓
Risk API
→ Module G
```

Equivalent linear flow:

```text
Module E → Immutable Feature Snapshot → Synthetic/Real Dataset Registry
→ Train/Validate → CatBoost/LightGBM → Calibration
→ Cost Policy → Hard Blocks → SHAP
→ Held-out Evaluation → Risk Prediction → Module G
```

---

# 1. Purpose and Non-Negotiable Decisions

The hackathon brief requires a working defensive detector/verifier or auto-responder with measured precision and recall on a held-out test set and explicit attention to false-positive cost.

No transaction-level dataset is supplied in the provided brief. Therefore, Module F must use a clearly disclosed controlled/synthetic benchmark derived from the canonical data contracts already implemented in Modules A-E, while keeping the training/evaluation pipeline replaceable when real labelled dispute data becomes available.

| Item | Decision |
|---|---|
| Primary ML target | Predict whether the evidence package is sufficiently complete, consistent and reliable to be **SAFE_TO_CONTEST**. |
| Important restriction | Do not train the primary model to predict bank dispute WON/LOST unless real, governed outcome labels later become available. |
| Positive label | **SAFE_TO_CONTEST** / contestable evidence package |
| Negative label | **NOT_SAFE_TO_AUTOMATE** / insufficient, contradictory or unreliable evidence |
| Model output | Calibrated `P(contestable)` |
| Business action | `ACCEPT`, `HUMAN_REVIEW` or `CONTEST` |
| Main metrics | Precision, Recall, F1, PR-AUC and false-positive cost |
| Primary candidate | CatBoost |
| Strong comparator | LightGBM |
| Baselines | Module E rule engine + Logistic Regression; Random Forest optional/secondary |
| Evaluation | Frozen held-out test set; no tuning on test |

---

# 2. Prerequisites from Module E

- Module E is complete and can produce one deterministic validation result per case.
- Each case has a stable `case_id` and references immutable Module D extraction outputs.
- Reason-code-specific evidence requirements are resolved before ML.
- Cross-document contradictions and missing/unknown evidence are represented explicitly.
- Module E can produce or persist a feature-ready snapshot/version identifier.
- Existing audit, RBAC, error and state-machine conventions remain authoritative.

## Required Module E input family → Examples used by Module F

| Required Module E input family | Examples used by Module F |
|---|---|
| Evidence coverage | `required_evidence_coverage`, `optional_evidence_coverage`, `missing_required_count` |
| Consistency | `amount_match`, `order_id_match`, `tracking_match`, `timeline_valid` |
| Contradictions | `contradiction_count`, `high_severity_contradictions`, `fatal_contradiction` |
| Document quality | `avg_ocr_confidence`, `min_ocr_confidence`, `document_quality_score` |
| Transaction context | `dispute_amount`, `disputed_amount_ratio`, `payment_method`, `days_to_deadline` |
| Case context | `reason_code`, `dispute_phase`, `shipment_available`, `refund_exists` |

---

# 3. Step 0 — Freeze the Module F Contracts Before Training

1. Create a versioned ML feature contract: `ml_features_v1`.
2. Create a versioned label policy: `contestability_label_v1`.
3. Create a dataset-generation specification: `synthetic_benchmark_v1`.
4. Define the split policy before any model is trained.
5. Define false-positive and false-negative costs before threshold tuning.
6. Define hard-block conditions that ML is never allowed to override.

### Why this step matters

A synthetic benchmark can be unintentionally shaped around a model. Freezing the feature, label, split and cost policies first makes the reported held-out metrics more defensible and reproducible.

| Artifact | Owner | Example version | Must be immutable after |
|---|---|---|---|
| Feature schema | ML/Data | `ml_features_v1` | Dataset freeze |
| Label policy | Risk/ML | `contestability_label_v1` | Dataset freeze |
| Synthetic generator config | ML/Data | `synthetic_benchmark_v1` | Benchmark generation |
| Split manifest | ML/Data | `split_v1` | Before model tuning |
| Decision cost policy | Risk/ML | `cost_policy_v1` | Before threshold optimization |

---

# 4. Step 1 — Define the Gold Label Policy

The label must represent evidence sufficiency for safe contest recommendation, not the eventual bank decision.

Define a documented adjudication policy that combines reason-code-specific mandatory evidence, material contradictions, timeline validity, document reliability and transaction consistency.

| Label | Meaning | Typical case |
|---|---|---|
| **1 — SAFE_TO_CONTEST** | Evidence package meets mandatory policy and contains no material blocker. | Required delivery evidence exists, key identifiers/amount/timeline agree, acceptable document confidence. |
| **0 — NOT_SAFE_TO_AUTOMATE** | Automated contest is unsafe because evidence is insufficient, contradictory or materially unreliable. | Missing mandatory proof, fatal amount/order mismatch, invalid timeline, very low-confidence evidence. |

- Store `label_source = synthetic_policy`, `manual_review`, or future `real_label`.
- Store `label_policy_version` for every labelled case.
- Do not use the same single scalar score as both feature and label rule; otherwise the model merely learns the rule that generated the label.
- Generate borderline cases and ambiguous cases; route ambiguous adjudication to `HUMAN_REVIEW` in later policy evaluation.
- `label = SAFE_TO_CONTEST` only when reason-specific mandatory evidence + consistency + quality conditions are satisfied and no fatal blocker exists.

---

# 5. Step 2 — Build `ml_features_v1` from Module E

One case becomes one ML row.

Module F must consume a frozen feature snapshot rather than live mutable database values. This creates train-serving consistency and allows exact reproduction of every prediction.

| Group | Feature | Type | Purpose |
|---|---|---|---|
| Evidence coverage | `required_evidence_coverage` | float 0-1 | High-value core feature |
| Evidence coverage | `missing_required_count` | integer | Missing mandatory evidence |
| Evidence coverage | `evidence_count` | integer | Number of usable evidence items |
| Consistency | `amount_match` | boolean/unknown | Payment/order/invoice consistency |
| Consistency | `order_id_match` | boolean/unknown | Cross-source order identifier |
| Consistency | `tracking_match` | boolean/unknown | Shipment/tracking relationship |
| Consistency | `customer_match_score` | float 0-1/unknown | Privacy-safe normalized similarity |
| Timeline | `timeline_valid` | boolean/unknown | Temporal relationship validation |
| Timeline | `days_delivery_to_dispute` | numeric/unknown | Delivery-dispute gap |
| Contradiction | `contradiction_count` | integer | Total contradictions |
| Contradiction | `high_severity_contradictions` | integer | Material contradictions |
| Quality | `avg_ocr_confidence` | float 0-1 | Aggregate extraction confidence |
| Quality | `min_ocr_confidence` | float 0-1 | Weakest critical evidence confidence |
| Quality | `document_quality_score` | float 0-1 | Module D/E quality aggregate |
| Context | `reason_code` | categorical | Reason-specific behavior |
| Context | `payment_method` | categorical | Payment context |
| Context | `dispute_amount` | numeric | Financial exposure |
| Context | `disputed_amount_ratio` | float | Disputed/captured amount |
| Context | `refund_exists` | boolean | Refund context |
| Context | `shipment_available` | boolean | Shipment evidence availability |
| Deadline | `days_to_deadline` | numeric | Operational urgency |

## Missing values

Do not replace `UNKNOWN` with `FALSE`.

Missing evidence, failed extraction and true negative evidence are different states. Preserve missingness explicitly and optionally create `*_is_missing` indicators for models that benefit from them.

---

# 6. Step 3 — Persist Immutable Feature Snapshots and Labels

| Table | Key columns | Purpose |
|---|---|---|
| `ml_feature_snapshots` | `feature_snapshot_id` PK, `case_id` FK, `feature_version`, `feature_json`, `source_hash`, `created_at` | Immutable model input for training/inference. |
| `ml_labels` | `label_id` PK, `case_id` FK, `target_name`, `label_value`, `label_source`, `label_policy_version`, `adjudicated_by`, `created_at` | Governed training labels. |
| `ml_datasets` | `dataset_id` PK, `name`, `dataset_version`, `feature_version`, `label_policy_version`, `generator_version`, `frozen_at` | Dataset registry. |
| `ml_dataset_members` | `dataset_id` FK, `case_id` FK, `feature_snapshot_id` FK, `label_id` FK, `split`, `group_key` | Exact train/validation/test membership. |

- Add `UNIQUE(case_id, feature_version, source_hash)` where appropriate to prevent accidental duplicate snapshots.
- Use `source_hash` to prove which Module E validation output produced the snapshot.
- Never update `feature_json` in-place after a model decision. Create a new snapshot/version.

---

# 7. Step 4 — Generate the Controlled Synthetic Benchmark

Because the challenge brief does not supply labelled dispute data, generate a benchmark from the same canonical schemas used by the application.

The generator should create realistic combinations, dependencies, missingness, noise and contradictions rather than independent random columns.

| Scenario family | Examples to generate | Why needed |
|---|---|---|
| Strong contestable | All mandatory evidence present; identifiers/amount/timeline agree; good OCR quality. | Clear positive cases. |
| Weak evidence | Missing proof of delivery, missing refund proof, incomplete required fields. | Clear negative cases. |
| Contradictory | Invoice amount differs; order ID mismatch; tracking belongs to different order. | False-positive protection. |
| Quality degraded | Blurred/rotated documents; low OCR confidence; partial extraction. | Robustness. |
| Partial refund | Refund exists but amount/timing varies; disputed amount differs from captured amount. | Financial edge cases. |
| Borderline | Coverage is high but one important field is unknown or moderately inconsistent. | Human-review band. |
| Deadline/status block | Expired deadline or invalid dispute lifecycle status. | Hard-block policy testing. |

### Dataset-generation requirements

- Target 5,000–10,000 cases if generation is inexpensive; smaller is acceptable for MVP if scenario coverage is strong.
- Prefer an imbalanced class distribution, e.g. roughly 25–40% contestable, rather than a perfectly balanced 50/50 dataset.
- Generate data through Modules A-E when feasible so the benchmark tests the real pipeline, not only a flat CSV generator.
- Keep a generator seed and `generator_version` for reproducibility.
- Create a manually inspected challenge subset, for example 200–500 cases, covering difficult scenarios.

**Important for ResolveAI:** This is the point where the synthetic dataset should be generated. The preferred architecture is **Module A → Module B → Module C → Module D → Module E → Module F dataset snapshot**, rather than creating an unrelated CSV directly inside Module F.

---

# 8. Step 5 — Freeze Train / Validation / Held-out Test

| Split | Recommended share | Permitted use |
|---|---:|---|
| TRAIN | 70% | Fit models and preprocessing artifacts. |
| VALIDATION | 15% | Hyperparameters, model choice, probability calibration and threshold selection. |
| TEST_HOLDOUT | 15% | Final one-time unbiased reporting. No tuning. |

- Use deterministic split manifests saved in `ml_dataset_members`.
- Stratify by contestability label and `reason_code` where possible.
- If cases share a synthetic customer/merchant/order/template family, group related cases so the same group does not leak across splits.
- Put some degraded/unseen combinations into held-out test to evaluate robustness.

---

# 9. Shipping Rule for the Frozen Test Set

After `TEST_HOLDOUT` is frozen, students must not inspect its labels to tune features, synthetic generator logic, hyperparameters, calibration method or thresholds.

If the benchmark definition changes, create `dataset_v2` and clearly report that version.

---

# 10. Step 6 — Data Validation and ML EDA

| Check | Required validation |
|---|---|
| Schema | All required features exist; expected types/ranges; category vocabulary controlled. |
| Label | Only governed 0/1 values; label source/version populated. |
| Duplicates | No duplicate case/feature snapshot memberships. |
| Leakage | No label-derived features, post-decision fields, contest outcome, reviewer decision or future timestamps. |
| Class distribution | Report per split and per reason code. |
| Missingness | Report missing/unknown frequency per feature and split. |
| Outliers | Inspect amount/time distributions; cap only when business-justified. |
| Shift | Compare train/validation/test distributions. |

- Save an EDA report as an artifact of the training run.
- Do not use overall accuracy as the primary metric for an imbalanced risk task.

---

# 11. Step 7 — Build the Reproducible Preprocessing Pipeline

| Feature type | CatBoost path | LightGBM / Logistic path |
|---|---|---|
| Categorical | Pass categorical column indices/names directly; fill missing category token consistently. | Categorical dtype/encoding for LightGBM; `OneHotEncoder` for Logistic Regression. |
| Numeric | Keep native numeric scale; impute only if model/pipeline requires. | Median/explicit sentinel; `StandardScaler` for Logistic Regression only. |
| Boolean/unknown | Use controlled values or numeric plus missing indicator. | Encode 0/1 plus missing indicator as needed. |
| High-cardinality IDs | Usually exclude raw case/order/customer IDs. | Exclude or engineer safe aggregate features; never memorize identity. |

- Fit preprocessing only on TRAIN.
- Reuse the fitted artifact on VALIDATION and TEST.
- Persist `feature_version` and preprocessing artifact hash with the model version.
- Do not apply SMOTE before splitting. For this task, prefer class weights initially.

---

# 12. Step 8 — Evaluate the Existing Module E Rule Baseline

The rule engine already implemented in Module E is **Baseline 0**.

Its performance is essential because the ML model must demonstrate measurable value over deterministic validation, not merely achieve a standalone score.

- Apply the rule baseline to VALIDATION and record Precision, Recall, F1, PR-AUC where scores are available, confusion matrix and business cost.
- Record rule coverage: how many cases receive deterministic contest/not-contest vs review/unknown.
- Store the results in `model_metrics` as a baseline run.

---

# 13. Step 9 — Train Logistic Regression Baseline

1. Build a `ColumnTransformer` or equivalent preprocessing pipeline.
2. Scale numeric variables; one-hot encode categorical features.
3. Use `class_weight="balanced"` as a baseline experiment if class imbalance is material.
4. Tune only a small validation grid for regularization strength.
5. Save validation probabilities, not just labels.
6. Record coefficients/top directional features for interpretability.

### Purpose

Logistic Regression gives a simple, auditable reference. If a complex tree model barely improves over it, the team should investigate feature/label quality before adding more complexity.

---

# 14. Step 10 — Train CatBoost as the Primary Candidate

| Parameter | Initial search range / recommendation |
|---|---|
| `loss_function` | `Logloss` |
| `eval_metric` | AUC / PRAUC plus custom reporting of precision/recall/F1 |
| `iterations` | 300–1500 with early stopping |
| `depth` | 4, 6, 8 |
| `learning_rate` | 0.02–0.10 |
| `l2_leaf_reg` | 3, 5, 10 |
| `random_strength` | 0–2 |
| `auto_class_weights` | Balanced as experiment; compare to unweighted |
| `random_seed` | Fixed and recorded |
| `od_type / early stopping` | Use validation set; stop before overfitting |

- Pass `reason_code` and other true categorical fields as CatBoost categorical features.
- Do not include `case_id`, `dispute_id`, raw document IDs or label-policy internals as predictors.
- Use TRAIN for fitting and VALIDATION for early stopping/model selection.
- Save `best_iteration`, feature list, hyperparameters, package version and training seed.

---

# 15. Step 11 — Train LightGBM as the Strong Comparator

| Parameter | Initial search range / recommendation |
|---|---|
| `objective` | `binary` |
| `metric` | `binary_logloss / auc`; calculate PR-AUC externally |
| `num_leaves` | 15, 31, 63 |
| `max_depth` | -1 or constrained values such as 4–8 |
| `learning_rate` | 0.02–0.10 |
| `n_estimators` | 300–1500 with early stopping |
| `min_child_samples` | 20–100 |
| `feature_fraction` | 0.7–1.0 |
| `bagging_fraction` | 0.7–1.0 |
| `class_weight / scale_pos_weight` | Evaluate only from TRAIN distribution |

- Use native categorical support or a fixed controlled encoding; do not fit category mappings on the full dataset.
- Keep the same split and feature contract used by CatBoost so comparison is fair.

### Embedded visual — Page 9

The original document's embedded image repeats the LightGBM parameter table and the model-comparison section. Its text is represented above and below in this Markdown so that no information contained in the image is lost.

---

# 16. Step 12 — Compare Models on Validation and Select the Winner

| Metric | Why it matters |
|---|---|
| Precision | How often an automated contest recommendation is actually safe; strongly related to false-positive risk. |
| Recall | How many safely contestable cases the model captures. |
| F1 | Balanced summary of precision and recall. |
| PR-AUC | Preferred ranking metric under class imbalance. |
| ROC-AUC | Secondary ranking metric; do not headline it alone. |
| Brier score | Probability quality after/before calibration. |
| Business cost | Explicit FP/FN/review cost used for decision policy. |

### Model comparison table

| Model | Precision | Recall | F1 | PR-AUC | FP Cost | Status |
|---|---:|---:|---:|---:|---:|---|
| Module E Rules | TBD | TBD | TBD | TBD | TBD | Baseline |
| Logistic Regression | TBD | TBD | TBD | TBD | TBD | Baseline |
| CatBoost | TBD | TBD | TBD | TBD | TBD | Candidate |
| LightGBM | TBD | TBD | TBD | TBD | TBD | Candidate |

Select the winner on **VALIDATION** based on the declared metric/cost policy, not on `TEST_HOLDOUT`.

---

# 17. Step 13 — Calibrate the Winning Model

1. Take the selected model without touching `TEST_HOLDOUT`.
2. Fit Platt/sigmoid calibration or isotonic calibration using a proper validation/calibration strategy.
3. Compare Brier score and calibration curve before/after calibration.
4. Persist the calibrator with the model artifact as one versioned inference package.
5. Use calibrated probability for all business thresholds and UI confidence displays.

### Important

A model score of `0.90` should not be presented as “90% confidence” unless calibration has been checked.

The UI should display calibrated contestability probability and model version.

---

# 18. Step 14 — Optimize the Cost-Sensitive ACCEPT / REVIEW / CONTEST Policy

```text
ExpectedCost = C_FP * FP + C_FN * FN + C_REVIEW * N_review
```

Optional amount-aware term:

```text
CaseLoss = P(incorrect_action) * disputed_amount + review_cost
```

- Define `C_FP` before threshold search; a false positive means recommending CONTEST when evidence is not safe enough.
- Evaluate candidate `T_accept` and `T_contest` pairs on VALIDATION.
- Require `T_accept < T_contest`; cases in-between go to HUMAN_REVIEW.
- Store the selected thresholds, cost assumptions and validation metrics in `model_decision_policies`.

| Probability band | Action | Rationale |
|---|---|---|
| `p < T_accept` | ACCEPT / do not auto-contest | Evidence is weak enough that contest is not recommended automatically. |
| `T_accept <= p < T_contest` | HUMAN_REVIEW | Uncertainty band protects against costly false positives. |
| `p >= T_contest` | CONTEST candidate | Only high-confidence, policy-compliant cases proceed. |

---

# 19. Step 15 — Apply Deterministic Hard Blocks After ML

```text
hard_block =
    mandatory_evidence_missing
    OR fatal_contradiction
    OR deadline_expired
    OR invalid_dispute_status

if hard_block:
    ML must not emit an automated CONTEST action
```

| Hard block | Source | Required outcome |
|---|---|---|
| Mandatory evidence missing | Module E | HUMAN_REVIEW or ACCEPT |
| Fatal contradiction | Module E | HUMAN_REVIEW; require correction/evidence |
| Deadline expired | Modules A/B current dispute state | No automated contest submission |
| Invalid/closed dispute status | Current authoritative dispute status | Block contest |
| Feature snapshot stale after evidence change | Module E/F lineage | Recompute validation + features + prediction |

### Design principle

**ML recommends; deterministic policy controls whether that recommendation is operationally safe.**

This prevents a high model score from overriding missing mandatory evidence or an invalid dispute lifecycle.

---

# 20. Step 16 — Generate SHAP Explanations

- For each CatBoost/LightGBM prediction, store top positive and negative feature contributions.
- Translate feature names to business-safe labels for the UI.
- Do not expose sensitive raw values or imply causality; SHAP explains model contribution, not legal truth.

| Example UI explanation | Underlying feature |
|---|---|
| Proof coverage strongly supports contest | `required_evidence_coverage` |
| No material contradictions detected | `high_severity_contradictions` |
| Invoice/order amount agreement supports case | `amount_match` |
| Low OCR confidence reduces confidence | `min_ocr_confidence` |
| Missing delivery proof reduces contestability | `missing_required_count / evidence coverage` |

---

# 21. Step 17 — Run the Frozen Held-out Test Exactly Once for Final Reporting

1. Load the frozen `TEST_HOLDOUT` manifest.
2. Load the selected model + preprocessing + calibrator + decision policy without retraining.
3. Generate probabilities, three-way decisions and hard-block outcomes.
4. Compute binary metrics for contestability and operational metrics for the three-way policy.
5. Save confusion matrix, PR curve, calibration curve, threshold/cost table and reason-code slices.
6. Persist the complete evaluation run and artifact hashes.

| Final report item | Required |
|---|---|
| Precision / Recall / F1 | Yes |
| PR-AUC | Yes |
| Confusion matrix | Yes |
| False-positive count and rate | Yes |
| False-negative count and rate | Yes |
| Explicit false-positive cost / total policy cost | Yes |
| Brier score / calibration result | Strongly recommended |
| Per-reason-code performance | Strongly recommended |
| Rule baseline vs ML improvement | Yes |
| Synthetic benchmark disclosure | Mandatory |

---

# 22. Step 18 — Module F Database Design

| Table | Important columns | Notes |
|---|---|---|
| `ml_feature_snapshots` | `feature_snapshot_id` PK, `case_id` FK, `feature_version`, `feature_json/jsonb`, `source_hash`, `created_at` | Immutable serving/training input. |
| `ml_labels` | `label_id` PK, `case_id` FK, `target_name`, `label_value`, `label_source`, `label_policy_version`, `reviewer/generator`, `created_at` | Govern label provenance. |
| `ml_datasets` | `dataset_id` PK, `dataset_version`, `feature_version`, `label_policy_version`, `generator_version`, `frozen_at`, `notes` | Registry for controlled benchmark versions. |
| `ml_dataset_members` | `dataset_id` FK, `case_id` FK, `feature_snapshot_id` FK, `label_id` FK, `split`, `group_key` | Exact split membership; unique case per dataset. |
| `model_versions` | `model_version_id` PK, `algorithm`, `artifact_uri`, `feature_version`, `preprocessing_hash`, `calibrator_uri`, `status`, `created_at` | Production model registry. |
| `model_training_runs` | `training_run_id` PK, `model_version_id` FK, `dataset_id` FK, `hyperparameters_json`, `seed`, `library_versions`, `started_at`, `finished_at` | Reproducibility. |
| `model_metrics` | `metric_id` PK, `training_run_id/eval_run` FK, `split`, `metric_name`, `metric_value`, `slice_name` | Metrics and slices. |
| `model_decision_policies` | `policy_id` PK, `model_version_id` FK, `t_accept`, `t_contest`, `c_fp`, `c_fn`, `c_review`, `policy_version`, `active` | Frozen action policy. |
| `risk_predictions` | `prediction_id` PK, `case_id` FK, `feature_snapshot_id` FK, `model_version_id` FK, `calibrated_probability`, `recommendation`, `hard_block`, `policy_id` FK, `created_at` | Never overwrite prior predictions. |
| `prediction_explanations` | `explanation_id` PK, `prediction_id` FK, `rank`, `feature_name`, `feature_value_safe`, `shap_value`, `direction` | Top SHAP contributions. |

- Index `risk_predictions(case_id, created_at DESC)`, `ml_dataset_members(dataset_id, split)`, and `ml_feature_snapshots(case_id, feature_version)`.
- Use JSONB for frozen feature payloads/hyperparameters but keep frequently queried governance fields as typed columns.
- Model artifacts live in controlled object storage; database stores immutable URIs and cryptographic hashes.

---

# 23. Step 19 — Implement Training and Inference Services

| Interface | Purpose | Minimum behavior |
|---|---|---|
| `POST /internal/ml/datasets/build` | Create/freeze benchmark version | Authorized offline/admin operation; returns `dataset_id` and split counts. |
| `POST /internal/ml/train` | Launch reproducible training run | References `dataset_id` + config; records run/version. |
| `POST /api/v1/cases/{case_id}/risk-score` | Score one evidence-ready case | Build/reuse immutable snapshot, predict, calibrate, apply policy, persist prediction. |
| `GET /api/v1/cases/{case_id}/risk` | Fetch latest/current prediction | Return probability, action, blockers, model/policy versions, explanation summary. |
| `GET /internal/ml/models/{id}/metrics` | Retrieve evaluation results | Return split metrics and selected slices. |

### Inference lineage

```text
Module E
   ↓
feature_snapshot
   ↓
preprocess
   ↓
model
   ↓
calibrator
   ↓
cost policy
   ↓
hard blocks
   ↓
risk_prediction
   ↓
Module G
```

- Inference must use the same `feature_version` and preprocessing artifact expected by `model_version`.
- If evidence changes, mark prior prediction stale and generate a new Module E validation/feature snapshot before rescoring.
- Make scoring idempotent by source snapshot/model/policy tuple where appropriate.

---

# 24. Step 20 — Handoff Contract to Module G

| Field | Description |
|---|---|
| `case_id` | Canonical case identifier. |
| `prediction_id` | Immutable Module F prediction. |
| `calibrated_probability` | `P(contestable)` after calibration. |
| `recommendation` | `ACCEPT / HUMAN_REVIEW / CONTEST`. |
| `hard_block` | Boolean and blocker codes. |
| `top_supporting_factors` | Safe SHAP/business explanation list. |
| `top_risk_factors` | Safe negative contributors. |
| `model_version` | Exact model artifact/version. |
| `decision_policy_version` | Threshold/cost policy version. |
| `feature_snapshot_id` | Exact Module E/F input snapshot. |

### Module G rule

RAG/LLM may draft a grounded response, but it must not silently change Module F recommendation or invent evidence.

If Module F returns `HUMAN_REVIEW` or a hard block, Module G should generate review assistance rather than an autonomous contest package.

---

# 25. Step 21 — Testing Requirements

| Test layer | Minimum tests |
|---|---|
| Unit | Feature conversion, missing values, hard blocks, threshold boundaries, cost calculations, probability validation. |
| Data contract | Feature names/types/ranges, category vocabulary, version compatibility, no forbidden leakage fields. |
| Training reproducibility | Same seed/config/dataset produces materially same metrics/model lineage. |
| Split integrity | No duplicate/group leakage across train/val/test. |
| Inference parity | Offline batch and API prediction agree for same snapshot/model/policy. |
| Calibration | Probability range valid; Brier/calibration artifacts produced. |
| Security | Only authorized roles can train/activate models; model files/hashes validated. |
| Regression | Golden challenge cases keep expected policy action unless version intentionally changes. |

---

# 26. Step 22 — Monitoring and Model Governance

- Log prediction latency, `model_version`, `policy_version` and `feature_version` without leaking sensitive raw evidence.
- Monitor feature missingness and distribution shift by `reason_code`.
- Track proportion of `ACCEPT / REVIEW / CONTEST` decisions and hard blocks.
- When real reviewer/outcome feedback becomes available, store it separately from the synthetic primary label and design a future dataset version.
- Never silently retrain or replace the active model. Require explicit promotion from candidate to active.
- Retain previous model/policy versions so historical decisions remain explainable.

---

# 27. Recommended Student Implementation Sequence

| # | Task | Deliverable | Priority |
|---:|---|---|---|
| 1 | Freeze feature/label/cost/split policies | `ml_features_v1` + policy specs | MUST |
| 2 | Implement feature snapshot + label tables | DB migration + repository/service | MUST |
| 3 | Implement synthetic benchmark generator | `dataset_v1` + scenario tests | MUST |
| 4 | Freeze split manifest | Train/val/test membership | MUST |
| 5 | EDA + leakage validation | Quality report | MUST |
| 6 | Rule baseline | Baseline metrics | MUST |
| 7 | Logistic Regression baseline | Model | MUST |
| 8 | CatBoost primary candidate | Model | MUST |
| 9 | LightGBM strong comparator | Model | STRONG |
| 10 | Model comparison | Validation report | MUST |
| 11 | Probability calibration | Calibrated model | MUST |
| 12 | Cost threshold policy | `T_accept/T_contest` policy | MUST |
| 13 | Hard-block integration | Safe 3-way action | MUST |
| 14 | SHAP explanation records/UI payload | Explanation output | STRONG |
| 15 | Frozen hold-out evaluation | Final honest metrics | MUST |
| 16 | Risk scoring API + persistence | Module F integrated | MUST |
| 17 | Monitoring/model registry governance | Governance | STRONG |

---

# 28. Recommended Repository Structure for Module F

```text
ml/
├── configs/
│   ├── feature_schema_v1.yaml
│   ├── label_policy_v1.yaml
│   ├── cost_policy_v1.yaml
│   ├── catboost_v1.yaml
│   └── lightgbm_v1.yaml
├── data/
│   ├── generator.py
│   ├── scenarios.py
│   ├── split.py
│   └── validation.py
├── features/
│   ├── builder.py
│   └── schema.py
├── training/
│   ├── baseline_rules.py
│   ├── logistic.py
│   ├── catboost_train.py
│   ├── lightgbm_train.py
│   ├── evaluate.py
│   ├── calibrate.py
│   └── threshold_policy.py
├── inference/
│   ├── predictor.py
│   ├── explainer.py
│   └── service.py
├── tests/
│   ├── test_features.py
│   ├── test_split_integrity.py
│   ├── test_threshold_policy.py
│   └── test_inference_parity.py
└── artifacts/
    └── # local dev only; production artifacts in object storage
```

---

# 29. Definition of Done for Module F

- [ ] Feature schema, label policy, split policy and cost policy are versioned and frozen.
- [ ] Synthetic benchmark is explicitly documented as synthetic/controlled and reproducible from a seed/config.
- [ ] Train/validation/held-out test membership is persisted and leakage-checked.
- [ ] Module E rule baseline and Logistic Regression baseline are evaluated.
- [ ] CatBoost is trained; LightGBM comparison is available or intentionally deferred with reason.
- [ ] Winning model is selected only on validation data.
- [ ] Probability calibration is measured and persisted.
- [ ] `ACCEPT / HUMAN_REVIEW / CONTEST` thresholds are selected from validation cost analysis.
- [ ] Hard blocks prevent unsafe automated `CONTEST` actions.
- [ ] SHAP or equivalent model explanations are available for tree-model predictions.
- [ ] Final held-out Precision, Recall, F1, PR-AUC and false-positive cost are frozen and reported.
- [ ] Every prediction references `case_id`, `feature_snapshot_id`, `model_version_id` and `policy_id`.
- [ ] Risk scoring API is idempotent/reproducible for the same snapshot/model/policy.
- [ ] Evidence change invalidates/stales old predictions and triggers re-validation before rescoring.
- [ ] Module G receives only controlled recommendation + grounded explanation metadata.

---

# 30. Minimum Demo Scenarios for Module F

| Case | Expected ML/policy behavior | What to show |
|---|---|---|
| Strong evidence | High calibrated p; `CONTEST` if no hard block. | Feature snapshot → prediction → top SHAP factors. |
| Missing mandatory evidence | Even if model score is high, hard block prevents automated contest. | Safety policy overrides ML. |
| Contradictory amount/order | Low p or blocker → `REVIEW/ACCEPT`. | Contradiction features and explanation. |
| Borderline evidence | p between thresholds → `HUMAN_REVIEW`. | Uncertainty band and false-positive protection. |
| Low OCR confidence | Reduced p / review depending on policy. | Module D quality flows through E into F. |

---

# 31. Required Hackathon Disclosure for the ML Results

### Recommended wording

> “The challenge brief did not provide transaction-level labelled dispute data. We therefore evaluated Module F on a controlled synthetic benchmark generated from the same canonical dispute, evidence and validation schemas used by the application. The train/validation/held-out test split was frozen before model tuning. Reported precision/recall and false-positive-cost results demonstrate the behavior of the proposed risk engine on this benchmark; production deployment would require validation and recalibration on governed real dispute data.”

---

# 32. Final Module F Flow

### Embedded diagram — Page 17

The original page-17 flow diagram contains the following sequence:

```text
Module E
Feature Snapshot
      ↓
Synthetic
Benchmark
      ↓
Freeze
Train/Val/Test
      ↓
Baselines
      ↓
CatBoost +
LightGBM
      ↓
Calibration
      ↓
Cost-based
Policy
      ↓
SHAP +
Hold-out Eval
      ↓
Risk API
→ Module G
```

### Exact textual flow represented by the document

```text
Module E
→ Immutable Feature Snapshot
→ Synthetic/Real Dataset Registry
→ Train/Validate
→ CatBoost/LightGBM
→ Calibration
→ Cost Policy
→ Hard Blocks
→ SHAP
→ Held-out Evaluation
→ Risk Prediction
→ Module G
```

---

# 33. Source Alignment

This implementation plan is aligned with the provided “AI Risk Manager” hackathon brief, which asks teams to build a working detector, verifier or auto-responder for one class of merchant loss with measured precision and recall on a held-out test set, including honest false-positive cost, under a defense-only constraint.

The brief does not specify or provide a training dataset in the supplied material; the controlled-benchmark strategy in this document is therefore an implementation decision for the current project, not a claim about Razorpay production data.

---

## Important ResolveAI Implementation Interpretation

### When does the ML part actually begin?

**Module F is the first module where actual Machine Learning training/inference occurs.**

The flow from the completed modules is:

```text
Module A
  ↓
Module B
  ↓
Module C
  ↓
Module D
  ↓
Module E
  ↓
Module F — ML
  ├─ Feature snapshot
  ├─ Synthetic benchmark
  ├─ Train / Validation / Test
  ├─ Logistic Regression baseline
  ├─ CatBoost
  ├─ LightGBM
  ├─ Calibration
  ├─ Cost-sensitive decision policy
  ├─ Hard blocks
  ├─ SHAP
  └─ Risk prediction
        ↓
Module G
```

### When should the synthetic dataset be generated?

The document explicitly places synthetic benchmark generation at **Module F Step 4**, after the feature/label/cost/split contracts have been frozen and the feature/label persistence foundation has been implemented.

However, the benchmark should **not** be an unrelated synthetic CSV.

The preferred ResolveAI architecture is:

```text
Synthetic Case Generation
        ↓
Module A
        ↓
Module B
        ↓
Module C
        ↓
Module D
        ↓
Module E
        ↓
Immutable Feature Snapshot
        ↓
ML Dataset Registry
        ↓
Train / Validation / TEST_HOLDOUT
        ↓
Module F Models
```

This means the synthetic cases should exercise the **real A→E pipeline**, allowing Module F to train on the same deterministic feature representation that production inference will consume.

The source document explicitly recommends generating data through Modules A-E when feasible and keeping the generator seed/version for reproducibility.

---

## Image/Text Placement Note

This Markdown intentionally converts the important text embedded in the document's images/diagrams into Markdown text at their corresponding locations:

- **Page 1:** architecture/overall Module F pipeline diagram.
- **Page 9:** embedded LightGBM/model-comparison visual content is represented as Markdown tables.
- **Page 17:** minimum-demo table, disclosure, final flow diagram, and source-alignment content are represented in their respective sections.

The source document is 17 pages and contains the complete Module F implementation blueprint.
