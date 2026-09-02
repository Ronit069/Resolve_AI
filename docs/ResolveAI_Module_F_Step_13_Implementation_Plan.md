# ResolveAI Module F — Step 13 Implementation Plan

## A. Step 13 Objective
**Objective:** The purpose of Step 13 is to calibrate the predicted probabilities of the *winning* machine learning model, transforming its raw outputs into mathematically rigorous confidence estimates that reflect real-world probability.
**Blueprint Purpose:** `ResolveAI_Module_F_Step_by_Step_ML_Implementation_Plan_with_Diagrams.md` section "17. Step 13 — Calibrate the Winning Model" explicitly dictates this step to ensure "probability quality after/before calibration". The calibrated probability is subsequently consumed by downstream business thresholds (Step 14) and UI confidence displays to ensure that a score of `0.90` actually corresponds to a `90%` safety confidence.

---

## B. Relationship to F0–F12
**Consumes:**
- **F5 VALIDATION**: Provides ground-truth labels and raw data for the deterministic split.
- **F12 Winner Report**: Identifies the formally selected winner.
- **CatBoost Raw Validation Artifact**: Provides the uncalibrated probabilities for the winning CatBoost model.

**Frozen:**
- All upstream F0-F12 modules, datasets, and models remain strictly frozen. 
- The premature historical F7 implementation (`backend/calibrate_and_optimize.py`) remains frozen as legacy code and is completely bypassed.

**Distinctions:**
- **Raw model probabilities**: Produced by frozen F6 CatBoost, consumed by Step 13.
- **Calibrated probabilities**: Produced purely by Step 13 via the calibration transformation mapping.
- **Decision thresholds**: Deferred to Step 14.
- **Business decisions**: Deferred to final evaluation.

---

## C. Step 12 Winner Dependency
Step 12 originally had an effective floating-point PR-AUC tie. Because the blueprint did not define an automatic tie-break hierarchy, the user manually resolved the tie.

**Resolved Architectural Decision:**
- The officially selected winner is **CATBOOST**.
- Step 13 operates strictly and only on the CatBoost model. 

Step 13 does not perform model comparison, does not infer a winner from old F7 artifacts, and does not select another winner.

---

## D. TEST_HOLDOUT Boundary
**NO TEST_HOLDOUT ACCESS ALLOWED.**
TEST_HOLDOUT remains completely inaccessible. The implementation must follow the repository's established `PermissionError` firewall convention.
There will be:
- no reading
- no parsing
- no hashing
- no statistics
- no calibration fitting
- no calibration evaluation
on `TEST_HOLDOUT`.

---

## E. Calibration Data Strategy
**Resolved Engineering Decision:**
The calibration strategy uses a deterministic stratified 50/50 split of the frozen F5 VALIDATION dataset, using `seed=42`. 

*Distinction:*
- **[BLUEPRINT REQUIREMENT]**: "proper validation/calibration strategy" / validation data only.
- **[ENGINEERING DECISION]**: Deterministic stratified 50/50 split using `seed=42` into `CALIBRATION_FIT_SET` and `CALIBRATION_EVALUATION_SET`.

The calibrator is fitted ONLY on `CALIBRATION_FIT_SET`.
The calibrator is evaluated ONLY on `CALIBRATION_EVALUATION_SET`.
We do not fit and evaluate on the same observations.

---

## F. Calibration Method
**Method:** `sklearn.isotonic.IsotonicRegression`
**Configuration:**
- `y_min=0.0`
- `y_max=1.0`
- `out_of_bounds="clip"`

**Behavior:**
- Input: Raw CatBoost winning-model probability.
- Target: Gold label (`1` / `0`).
- Bounds: `[0.0, 1.0]`.
- Output: Calibrated probability.

---

## G. Exact Data Flow
```text
F5 frozen VALIDATION
        ↓
deterministic stratified 50/50 split (seed=42)
        ↓
+------------------------------+
|                              |
v                              v
CALIBRATION_FIT_SET     CALIBRATION_EVALUATION_SET
        |                              |
        v                              |
fit IsotonicRegression                 |
        |                              |
        +--------------+---------------+
                       ↓
              calibrated probabilities
                       ↓
            Brier / reliability metrics
                       ↓
                 artifacts
```
*Note: TEST_HOLDOUT is NOT part of this flow.*

---

## H. Metrics
Step 13 requires calculating mathematically correct metrics based on the evaluation subset:
- **Brier-before**: Mathematically correct calculation of the mean squared error on raw CatBoost probabilities.
- **Brier-after**: Mathematically correct calculation of the mean squared error on calibrated probabilities. (Note: Brier-after may be higher, lower, or equal. Improvement is NOT a required acceptance criterion).
- **Calibration/Reliability Curve**: Mathematically correct calculation of empirical true-positive rates across bins.
- **Observation Isolation**: Validating metrics strictly on `CALIBRATION_EVALUATION_SET`.
- **Bounds**: Validating all calibrated probabilities fall strictly within `[0.0, 1.0]`.

---

## I. Threshold Handling
Step 13 **does not select, evaluate, or optimize** any decision threshold. 
It preserves no historical F7 threshold. It outputs strictly continuous `[0.0, 1.0]` floating-point values. Thresholding belongs absolutely to Step 14.

---

## J. Cost Policy
Cost policy (`C_FP`, `C_FN`, `C_REVIEW`, `N_review`) is **not applicable** to Step 13.
Step 13 solely concerns statistical probability mapping. Cost policies are threshold-dependent business functions applied in Step 14.

---

## K. Hard-Block Handling
F1 deterministic hard blocks (missing evidence, fatal contradictions) operate orthogonally to statistical ML predictions. They are completely bypassed and ignored during the mathematical calibration of probabilities. 

---

## L. Leakage Controls
- **TEST_HOLDOUT Leakage**: Hard-blocked via file path `PermissionError` firewall.
- **Calibration Leakage**: Blocked by explicitly splitting the data into disjoint `CALIBRATION_FIT_SET` and `CALIBRATION_EVALUATION_SET`.
- **Retraining Leakage**: CatBoost `.fit()` is never called. The underlying model is treated as a strictly frozen inference generator.
- **Threshold Leakage**: No discrete threshold classifications occur.
- **Future Information / Production**: Data is strictly confined to the frozen offline `VALIDATION` JSONL.

---

## M. Artifacts
All generated in `artifacts/step13_calibration_[timestamp]_v1/`:
1. **`calibrator.pkl`**: The fitted Scikit-Learn IsotonicRegression object.
2. **`calibration_metrics.json`**: Provenance metadata.
3. **`calibrated_validation_probabilities.csv`**: Contains exclusively:
   - `example_id`
   - `true_label`
   - `raw_p_safe_to_contest`
   - `calibrated_p_safe_to_contest`

*(No unnecessary identifiers are introduced).*

---

## N. Provenance
`calibration_metrics.json` will contain actual provenance reflecting:
- winner = CatBoost
- winner selection = manual Step 12 resolution
- calibration method = IsotonicRegression
- isotonic configuration
- random seed = 42
- split method = stratified 50/50 split
- source VALIDATION dataset hash
- source CatBoost probability artifact hash
- calibration-fit subset hash
- calibration-evaluation subset hash
- observation counts (fit vs. evaluation)
- Brier scores (before vs. after)
- calibration curve information
- timestamp
- Python version
- sklearn version

*(TEST_HOLDOUT hash = NOT COMPUTED)*

---

## O. Exact Files
| File | Status | Responsibility |
|------|--------|----------------|
| `backend/calibrate_winner_step13.py` | [NEW] | Executes Step 13 pipeline logic. |
| `backend/tests/test_module_f_step13.py` | [NEW] | Verifies Step 13 invariants. |
| `backend/app/services/ml/training/calibration.py` | [NO CHANGE] | Reused strictly for the Isotonic calibrator logic (unless inspection proves modification is absolutely required). |
| `backend/compare_models_step12.py` | [NO CHANGE] | Remains frozen. |
| `backend/calibrate_and_optimize.py` | [NO CHANGE] | Legacy F7, explicitly superseded and ignored. |

*(All F0-F12 files remain frozen. No database migration is introduced).*

---

## P. Testing Plan
Detailed `pytest` strategy covering at minimum:
1. CatBoost is accepted as the explicitly selected winner.
2. No unresolved-winner blocker remains.
3. Deterministic stratified 50/50 split is executed correctly.
4. Same input produces the same split.
5. Fit/evaluation observations are disjoint.
6. Union equals VALIDATION observations.
7. Label stratification is preserved.
8. Isotonic calibrator fits only on calibration-fit observations.
9. Evaluation occurs only on calibration-evaluation observations.
10. TEST_HOLDOUT raises `PermissionError`.
11. TEST_HOLDOUT is never hashed.
12. CatBoost `.fit()` is never called.
13. No Logistic Regression training occurs.
14. No LightGBM training occurs.
15. Raw probabilities remain unchanged.
16. Calibrated probabilities are within `[0.0, 1.0]`.
17. Brier score calculation is mathematically correct. *(Does NOT require Brier-after < Brier-before).*
18. Calibration curve calculation is mathematically correct.
19. Artifacts are created correctly with the exact specified schema.
20. Metadata provenance is correctly populated.
21. Hashes are correctly computed (excluding holdout).
22. F7 legacy implementation is not invoked.
23. Step 14 threshold optimization is not invoked.
24. Existing F0-F12 remain untouched.
25. Repeated execution is 100% deterministic.

---

## Q. Acceptance Criteria
Step 13 can become **COMPLETE** and **FROZEN** when:
- CatBoost is explicitly confirmed as the Step 12 winner by the pipeline.
- The deterministic 50/50 stratified validation split is used.
- The calibrator is fitted only on the calibration-fit subset.
- Calibration evaluation uses only the evaluation subset.
- TEST_HOLDOUT remains completely untouched.
- CatBoost is not retrained.
- Isotonic calibration is correctly performed.
- Metrics are mathematically correct.
- Required artifacts are generated.
- Provenance is complete.
- Step 13 tests pass.
- Full regression passes.
- F0-F12 remain strictly frozen.
- Step 14 remains unimplemented.

---

## R. Explicit Scope Boundary

### STEP 13 DOES:
- calibrates the selected CatBoost model's raw probabilities
- uses deterministic stratified 50/50 VALIDATION calibration split
- fits IsotonicRegression
- evaluates calibration quality
- persists calibrator and provenance artifacts

### STEP 13 DOES NOT:
- retrain CatBoost
- compare models
- access TEST_HOLDOUT
- optimize thresholds
- implement Step 14
- apply hard blocks
- modify F0-F12
- invoke legacy F7 threshold/calibration workflow

### NEXT BLUEPRINT STEP:
**Step 14 — Optimize the Cost-Sensitive ACCEPT / REVIEW / CONTEST Policy.**

---
`PLAN ONLY — NO IMPLEMENTATION PERFORMED.`
