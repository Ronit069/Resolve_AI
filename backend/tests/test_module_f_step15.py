"""
Tests for evaluate_holdout_step15.py — the one-time authorized final
holdout evaluation. Runs against the REAL, currently-on-disk pipeline
artifacts (champion model, calibrator, decision policy, test_holdout
split) rather than mocking them out, so these tests double as a
regression check that the reused calculate_3way_cost/get_latest_dir
functions and the locked cost constants still produce internally
consistent numbers.

Any artifacts/step15_final_holdout_eval_* directory created while these
tests run is removed again at module teardown.
"""

import os
import glob
import json
import shutil
import hashlib
import time

import pytest

import evaluate_holdout_step15 as step15
from train_benchmark import load_jsonl as train_load_jsonl
from calibrate_winner_step13 import hash_file as step13_hash_file, load_jsonl as step13_load_jsonl
from optimize_thresholds_step14 import hash_file as step14_hash_file, calculate_3way_cost

TEST_HOLDOUT_FILE = "synthetic_benchmark_v1_test_holdout.jsonl"


@pytest.fixture(autouse=True, scope="module")
def cleanup_step15_artifacts():
    before = set(glob.glob("artifacts/step15_final_holdout_eval_*"))
    yield
    after = set(glob.glob("artifacts/step15_final_holdout_eval_*"))
    for d in after - before:
        shutil.rmtree(d, ignore_errors=True)


def _run_step15():
    before = set(glob.glob("artifacts/step15_final_holdout_eval_*"))
    step15.main()
    new_dirs = set(glob.glob("artifacts/step15_final_holdout_eval_*")) - before
    assert len(new_dirs) == 1, "Expected exactly one new Step 15 output directory"
    out_dir = new_dirs.pop()
    with open(os.path.join(out_dir, "final_evaluation.json")) as f:
        return json.load(f), out_dir


@pytest.fixture(scope="module")
def evaluation():
    data, out_dir = _run_step15()
    return data


def test_upstream_firewall_still_forbids_test_holdout():
    """
    Step 15 must be the ONLY authorized reader of test_holdout. Every
    upstream script's own firewall must still raise, proving Step 15 was
    added without weakening any existing guard.
    """
    with pytest.raises(PermissionError):
        train_load_jsonl(TEST_HOLDOUT_FILE)
    with pytest.raises(PermissionError):
        step13_hash_file(TEST_HOLDOUT_FILE)
    with pytest.raises(PermissionError):
        step13_load_jsonl(TEST_HOLDOUT_FILE)
    with pytest.raises(PermissionError):
        step14_hash_file(TEST_HOLDOUT_FILE)


def test_train_and_validation_loading_unchanged():
    """
    Step 15 must not have altered existing TRAIN/VALIDATION loading
    behavior in the scripts it imports from or sits alongside.
    """
    train_data = train_load_jsonl("synthetic_benchmark_v1_train.jsonl")
    val_data = step13_load_jsonl("synthetic_benchmark_v1_validation.jsonl")
    assert len(train_data) == 7108
    assert len(val_data) == 1484


def test_artifact_contains_required_fields(evaluation):
    for top_key in (
        "step", "timestamp", "python_version", "git_commit",
        "champion_model", "calibration", "decision_policy",
        "test_holdout_dataset", "metrics", "reproducibility",
    ):
        assert top_key in evaluation, f"missing top-level field: {top_key}"

    for metric_key in (
        "accuracy", "precision", "recall", "f1", "confusion_matrix",
        "accept_count", "review_count", "contest_count", "expected_cost",
        "cost_breakdown", "cost_fn_count", "brier_score_raw", "brier_score_calibrated",
    ):
        assert metric_key in evaluation["metrics"], f"missing metrics field: {metric_key}"

    for cm_key in ("tp", "tn", "fp", "fn"):
        assert cm_key in evaluation["metrics"]["confusion_matrix"]


def test_metrics_mathematically_consistent_with_confusion_counts(evaluation):
    cm = evaluation["metrics"]["confusion_matrix"]
    n = evaluation["test_holdout_dataset"]["example_count"]

    assert cm["tp"] + cm["fp"] + cm["tn"] + cm["fn"] == n

    expected_precision = cm["tp"] / (cm["tp"] + cm["fp"]) if (cm["tp"] + cm["fp"]) > 0 else 0.0
    expected_recall = cm["tp"] / (cm["tp"] + cm["fn"]) if (cm["tp"] + cm["fn"]) > 0 else 0.0
    expected_f1 = (
        2 * expected_precision * expected_recall / (expected_precision + expected_recall)
        if (expected_precision + expected_recall) > 0 else 0.0
    )

    assert evaluation["metrics"]["precision"] == pytest.approx(expected_precision, abs=1e-9)
    assert evaluation["metrics"]["recall"] == pytest.approx(expected_recall, abs=1e-9)
    assert evaluation["metrics"]["f1"] == pytest.approx(expected_f1, abs=1e-9)

    expected_accuracy = (cm["tp"] + cm["tn"]) / n
    assert evaluation["metrics"]["accuracy"] == pytest.approx(expected_accuracy, abs=1e-9)

    assert (
        evaluation["metrics"]["accept_count"]
        + evaluation["metrics"]["review_count"]
        + evaluation["metrics"]["contest_count"]
        == n
    )


def test_expected_cost_matches_reused_cost_function(evaluation):
    """
    Independently recompute expected_cost by calling the REUSED
    calculate_3way_cost against the real test_holdout examples, and
    confirm it matches the artifact exactly. Also confirms the reported
    cost_breakdown sums to expected_cost (catches the metric-FN vs
    cost-FN mixup this script must not repeat).
    """
    import pandas as pd
    import numpy as np
    import catboost
    import pickle

    from app.services.ml.training.preprocessor import Preprocessor

    policy = evaluation["decision_policy"]
    calib_dir = os.path.join("artifacts", evaluation["calibration"]["step13_dir"])
    run_dir = os.path.join("artifacts", "runs", evaluation["champion_model"]["run_dir"])

    with open(os.path.join(calib_dir, "calibrator.pkl"), "rb") as f:
        calibrator = pickle.load(f)

    model = catboost.CatBoostClassifier()
    model.load_model(os.path.join(run_dir, "model.cbm"))

    test_data = step15.load_test_holdout(TEST_HOLDOUT_FILE)
    preprocessor = Preprocessor()
    preprocessor.fit(test_data)
    X_test, y_test = preprocessor.transform(test_data)

    raw_probs = model.predict_proba(X_test)[:, 1]
    calibrated_probs = np.clip(calibrator.predict(raw_probs), 0.0, 1.0)

    df = pd.DataFrame({"true_label": y_test.values, "calibrated_p_safe_to_contest": calibrated_probs})
    recomputed = calculate_3way_cost(
        df, policy["t_accept"], policy["t_contest"], policy["c_fp"], policy["c_fn"], policy["c_review"]
    )

    assert recomputed["expected_cost"] == pytest.approx(evaluation["metrics"]["expected_cost"], abs=1e-6)
    assert recomputed["precision"] == pytest.approx(evaluation["metrics"]["precision"], abs=1e-9)
    assert recomputed["recall"] == pytest.approx(evaluation["metrics"]["recall"], abs=1e-9)

    breakdown_sum = sum(evaluation["metrics"]["cost_breakdown"].values())
    assert breakdown_sum == pytest.approx(evaluation["metrics"]["expected_cost"], abs=1e-6)


def test_provenance_fields_populated(evaluation):
    def is_sha256(s):
        return isinstance(s, str) and len(s) == 64 and all(c in "0123456789abcdef" for c in s)

    assert is_sha256(evaluation["champion_model"]["model_cbm_sha256"])
    assert is_sha256(evaluation["calibration"]["calibrator_pkl_sha256"])
    assert is_sha256(evaluation["decision_policy"]["decision_policy_json_sha256"])
    assert is_sha256(evaluation["test_holdout_dataset"]["sha256"])

    assert evaluation["git_commit"] and evaluation["git_commit"] != "UNAVAILABLE"
    assert len(evaluation["git_commit"]) == 40

    assert evaluation["champion_model"]["run_dir"]
    assert evaluation["calibration"]["step13_dir"]
    assert evaluation["decision_policy"]["step14_dir"]
    assert evaluation["timestamp"]


def test_rerun_is_deterministic_except_timestamp_fields():
    """
    The output directory name (and the artifact's own "timestamp" field)
    is second-granularity, matching every other step in this pipeline
    (step13/step14 use the identical convention) — so two runs launched
    within the same wall-clock second legitimately collide on the same
    directory. The sleeps below only guarantee this test's own two calls,
    and the preceding call made by the `evaluation` fixture, land in
    distinct seconds; they are not a wait for any external condition.
    """
    time.sleep(1.1)
    first, first_dir = _run_step15()
    time.sleep(1.1)
    second, second_dir = _run_step15()

    def strip_volatile(d):
        d = json.loads(json.dumps(d))
        d.pop("timestamp", None)
        return d

    assert strip_volatile(first) == strip_volatile(second)
    assert first["timestamp"] != second["timestamp"] or first_dir != second_dir


def test_no_fake_or_hardcoded_values(evaluation):
    """
    Independently recompute the test_holdout file's own SHA256 and its
    real example/positive counts directly from disk, bypassing the
    evaluator entirely, and confirm the artifact's reported numbers were
    not hand-typed/fabricated.
    """
    hasher = hashlib.sha256()
    with open(TEST_HOLDOUT_FILE, "rb") as f:
        hasher.update(f.read())
    real_hash = hasher.hexdigest()

    real_count = 0
    real_positive = 0
    with open(TEST_HOLDOUT_FILE) as f:
        for line in f:
            ex = json.loads(line)
            real_count += 1
            if ex["label"] == 1:
                real_positive += 1

    assert evaluation["test_holdout_dataset"]["sha256"] == real_hash
    assert evaluation["test_holdout_dataset"]["example_count"] == real_count
    assert evaluation["test_holdout_dataset"]["positive_count"] == real_positive
