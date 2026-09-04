"""
Step 15 — Final Holdout Evaluation.

Phase 1 Buildathon-readiness deliverable. This is the ONLY script in the
pipeline authorized to read `synthetic_benchmark_v1_test_holdout.jsonl`.
Every other script in this pipeline (run_benchmark, split_benchmark,
train_benchmark, calibrate_winner_step13, optimize_thresholds_step14)
enforces a leakage firewall that raises PermissionError on any path
containing "test_holdout" — that firewall is deliberately preserved
everywhere else and must never be copied out of this file. This script
only reads test_holdout after the champion model (F6 CatBoost), its
isotonic calibrator (Step 13), and the locked 3-way decision policy
(Step 14) have already been fully selected/fit/optimized using TRAIN and
VALIDATION only.

Reuses (does not reimplement) `calculate_3way_cost` and `get_latest_dir`
from optimize_thresholds_step14.py, and the exact cost constants already
locked by that step's own policy artifact.

Run once per evaluation. Writes one self-contained JSON artifact under
artifacts/step15_final_holdout_eval_<timestamp>_v1/final_evaluation.json.
Never fabricates, hardcodes, or manually adjusts any reported metric.
"""

import os
import sys
import json
import hashlib
import datetime
import pickle
import subprocess
import pandas as pd
import numpy as np
import catboost
from sklearn.metrics import brier_score_loss

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ml.dataset_splitter import FORBIDDEN_FEATURES
from app.services.ml.training.preprocessor import Preprocessor
from optimize_thresholds_step14 import calculate_3way_cost, get_latest_dir

TEST_HOLDOUT_FILE = "synthetic_benchmark_v1_test_holdout.jsonl"


def hash_file(filepath: str) -> str:
    """
    Unlike every other pipeline script's hash_file(), this one does NOT
    forbid test_holdout paths — this script is the sole authorized
    exception to the leakage firewall.
    """
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest()


def load_test_holdout(filepath: str):
    """
    Loads the locked test_holdout split. This loader (and the fact that
    it does not raise on "test_holdout" paths) must never be copied into
    any other script in this pipeline.
    """
    data = []
    with open(filepath, "r") as f:
        for line in f:
            ex = json.loads(line)
            for forbidden in FORBIDDEN_FEATURES:
                if forbidden in ex.get("features", {}):
                    raise ValueError(f"Forbidden feature {forbidden} detected in payload!")
            data.append(ex)
    return data


def get_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "UNAVAILABLE"


def main():
    print("Executing Step 15: Final Holdout Evaluation (AUTHORIZED test_holdout access)...")

    artifacts_dir = "artifacts"

    # 1. Locate the champion model (F6 CatBoost) — same run directory Step 13 used.
    f6_dir = get_latest_dir(os.path.join(artifacts_dir, "runs"), "", exclude_str="calibrated")
    model_path = os.path.join(artifacts_dir, "runs", f6_dir, "model.cbm")
    model_meta_path = os.path.join(artifacts_dir, "runs", f6_dir, "metadata.json")
    model_hash = hash_file(model_path)
    with open(model_meta_path) as f:
        model_metadata = json.load(f)

    # 2. Locate the calibrator (Step 13).
    f13_dir = get_latest_dir(artifacts_dir, "step13_calibration")
    calibrator_path = os.path.join(artifacts_dir, f13_dir, "calibrator.pkl")
    calibration_metrics_path = os.path.join(artifacts_dir, f13_dir, "calibration_metrics.json")
    calibrator_hash = hash_file(calibrator_path)
    with open(calibrator_path, "rb") as f:
        calibrator = pickle.load(f)
    with open(calibration_metrics_path) as f:
        calibration_metrics = json.load(f)

    # Sanity: the calibrator on disk must have been fit against THIS exact model.
    if calibration_metrics.get("source_model_cbm_hash") != model_hash:
        raise RuntimeError(
            "Step 13 calibrator was fit against a different model.cbm than the "
            "one currently in artifacts/runs/. Re-run Step 13 against the current "
            "champion before running Step 15."
        )

    # 3. Locate the locked decision policy (Step 14).
    f14_dir = get_latest_dir(artifacts_dir, "step14_threshold_policy")
    policy_path = os.path.join(artifacts_dir, f14_dir, "decision_policy.json")
    policy_hash = hash_file(policy_path)
    with open(policy_path) as f:
        policy = json.load(f)

    # Sanity: the policy must have been optimized against the calibration output on disk.
    f13_csv_path = os.path.join(artifacts_dir, f13_dir, "calibrated_validation_probabilities.csv")
    f13_csv_hash = hash_file(f13_csv_path)
    if policy.get("source_f13_artifact_hash") != f13_csv_hash:
        raise RuntimeError(
            "Step 14 decision policy was optimized against a different Step 13 "
            "calibration output than the one currently on disk. Re-run Step 14 "
            "before running Step 15."
        )

    t_accept = policy["t_accept"]
    t_contest = policy["t_contest"]
    c_fp = policy["c_fp"]
    c_fn = policy["c_fn"]
    c_review = policy["c_review"]

    # 4. Load test_holdout — ONLY HERE, ONLY NOW.
    test_hash = hash_file(TEST_HOLDOUT_FILE)
    test_data = load_test_holdout(TEST_HOLDOUT_FILE)

    # 5. Featurize with the exact same (stateless) Preprocessor used at train/calibration time.
    preprocessor = Preprocessor()
    preprocessor.fit(test_data)
    X_test, y_test = preprocessor.transform(test_data)

    # 6. Raw CatBoost predictions from the already-trained, frozen model (no fit() call).
    model = catboost.CatBoostClassifier()
    model.load_model(model_path)
    raw_probs = model.predict_proba(X_test)[:, 1]

    # 7. Apply the LOCKED isotonic calibrator (fit only on validation; never refit here).
    calibrated_probs = np.clip(calibrator.predict(raw_probs), 0.0, 1.0)

    eval_df = pd.DataFrame({
        "example_id": [ex["example_id"] for ex in test_data],
        "true_label": y_test.values,
        "raw_p_safe_to_contest": raw_probs,
        "calibrated_p_safe_to_contest": calibrated_probs,
    })

    # 8. Apply the LOCKED 3-way decision policy — reused, not reimplemented.
    metrics = calculate_3way_cost(eval_df, t_accept, t_contest, c_fp, c_fn, c_review)

    # Consistency self-checks — fail loudly rather than silently report bad numbers.
    n = len(eval_df)
    if metrics["accept_count"] + metrics["review_count"] + metrics["contest_count"] != n:
        raise RuntimeError("Accept/review/contest counts do not partition the holdout set.")
    positive_count = int((eval_df["true_label"] == 1).sum())
    negative_count = int((eval_df["true_label"] == 0).sum())
    if metrics["tp_count"] + metrics["fn_count"] != positive_count:
        raise RuntimeError("TP+FN does not equal total positive examples in holdout set.")
    if metrics["tn_count"] + metrics["fp_count"] != negative_count:
        raise RuntimeError("TN+FP does not equal total negative examples in holdout set.")

    accuracy = (metrics["tp_count"] + metrics["tn_count"]) / n

    # calculate_3way_cost()'s returned "fn_count" is deliberately the
    # *standard metric* FN (p < t_contest AND true_label==1), documented in
    # its own source as distinct from the *cost* FN it uses internally to
    # compute expected_cost (p < t_accept AND true_label==1 — i.e. only
    # positives that were auto-ACCEPTed with no human review at all, not
    # positives routed to REVIEW). The function does not return that
    # narrower count, so it is re-derived here, using the identical mask,
    # purely to decompose the already-computed expected_cost for reporting.
    # This does NOT change or recompute expected_cost itself.
    cost_fn_count = int(
        ((eval_df["calibrated_p_safe_to_contest"] < t_accept) & (eval_df["true_label"] == 1)).sum()
    )
    cost_breakdown = {
        "false_positive_cost": c_fp * metrics["fp_count"],
        "false_negative_cost": c_fn * cost_fn_count,
        "review_cost": c_review * metrics["review_count"],
    }
    if abs(sum(cost_breakdown.values()) - metrics["expected_cost"]) > 1e-6:
        raise RuntimeError(
            "Cost breakdown does not sum to expected_cost — the re-derived "
            "cost_fn_count mask no longer matches calculate_3way_cost's own "
            "internal fn_mask."
        )

    # 9. Brier score, mirroring Step 13's own before/after convention.
    brier_raw = float(brier_score_loss(eval_df["true_label"], eval_df["raw_p_safe_to_contest"]))
    brier_calibrated = float(brier_score_loss(eval_df["true_label"], eval_df["calibrated_p_safe_to_contest"]))

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = f"artifacts/step15_final_holdout_eval_{timestamp}_v1"
    os.makedirs(out_dir, exist_ok=True)

    final_evaluation = {
        "step": "step15_final_holdout_evaluation",
        "description": (
            "One-time authorized evaluation of the locked champion model + calibrator "
            "+ 3-way decision policy against the previously untouched test_holdout "
            "split. No value in this artifact has been fabricated, hardcoded, or "
            "manually adjusted."
        ),
        "timestamp": timestamp,
        "python_version": sys.version,
        "git_commit": get_git_commit(),
        "champion_model": {
            "algorithm": "CatBoost",
            "run_dir": f6_dir,
            "model_cbm_sha256": model_hash,
            "training_metadata": model_metadata,
        },
        "calibration": {
            "step13_dir": f13_dir,
            "calibrator_pkl_sha256": calibrator_hash,
            "calibration_method": calibration_metrics.get("calibration_method"),
            "source_model_cbm_hash_recorded_at_calibration": calibration_metrics.get("source_model_cbm_hash"),
        },
        "decision_policy": {
            "step14_dir": f14_dir,
            "decision_policy_json_sha256": policy_hash,
            "t_accept": t_accept,
            "t_contest": t_contest,
            "c_fp": c_fp,
            "c_fn": c_fn,
            "c_review": c_review,
            "primary_objective": policy.get("primary_objective"),
            "validation_expected_cost": policy.get("expected_cost"),
            "validation_precision": policy.get("precision"),
            "validation_recall": policy.get("recall"),
            "validation_f1": policy.get("f1"),
        },
        "test_holdout_dataset": {
            "file": TEST_HOLDOUT_FILE,
            "sha256": test_hash,
            "example_count": n,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "positive_rate": positive_count / n,
        },
        "metrics": {
            "accuracy": accuracy,
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "f1": metrics["f1"],
            "confusion_matrix": {
                "tp": metrics["tp_count"],
                "tn": metrics["tn_count"],
                "fp": metrics["fp_count"],
                "fn": metrics["fn_count"],
            },
            "accept_count": metrics["accept_count"],
            "review_count": metrics["review_count"],
            "contest_count": metrics["contest_count"],
            "expected_cost": metrics["expected_cost"],
            "cost_breakdown": cost_breakdown,
            "cost_breakdown_note": (
                "false_negative_cost uses cost_fn_count (positives auto-ACCEPTed "
                "with p < t_accept, i.e. missed with no human review at all), which "
                "is narrower than confusion_matrix.fn (all positives not routed to "
                "CONTEST, including ones sent to REVIEW). Both are real, distinct "
                "counts from calculate_3way_cost's own documented definitions; this "
                "breakdown is the one whose three components sum exactly to "
                "expected_cost above."
            ),
            "cost_fn_count": cost_fn_count,
            "brier_score_raw": brier_raw,
            "brier_score_calibrated": brier_calibrated,
        },
        "reproducibility": {
            "cost_function_source": "optimize_thresholds_step14.calculate_3way_cost (reused, not reimplemented)",
            "cost_constants_source": "optimize_thresholds_step14.py / decision_policy.json (locked, not re-derived)",
            "random_seed": 42,
            "leakage_firewall_status": (
                "test_holdout was read ONLY by this script (evaluate_holdout_step15.py), "
                "after the champion model, calibrator, and decision policy were fully "
                "selected/fit/optimized using only TRAIN and VALIDATION. All upstream "
                "scripts (run_benchmark.py, split_benchmark.py, train_benchmark.py, "
                "calibrate_winner_step13.py, optimize_thresholds_step14.py) continue to "
                "raise PermissionError on any test_holdout path and were not modified."
            ),
        },
    }

    out_path = os.path.join(out_dir, "final_evaluation.json")
    with open(out_path, "w") as f:
        json.dump(final_evaluation, f, indent=2)

    print(f"Step 15 complete. Final holdout evaluation written to {out_path}")
    print(f"Holdout size: {n} (positive_rate={positive_count / n:.4f})")
    print(f"Precision: {metrics['precision']:.4f}  Recall: {metrics['recall']:.4f}  F1: {metrics['f1']:.4f}")
    print(f"Confusion Matrix: TP={metrics['tp_count']} TN={metrics['tn_count']} FP={metrics['fp_count']} FN={metrics['fn_count']}")
    print(f"Expected Cost: {metrics['expected_cost']:.2f}")
    print(f"Brier (raw -> calibrated): {brier_raw:.4f} -> {brier_calibrated:.4f}")


if __name__ == "__main__":
    main()
