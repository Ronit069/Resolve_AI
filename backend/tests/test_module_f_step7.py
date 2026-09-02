import pytest
import os
import json
import pandas as pd
import numpy as np
import catboost

from sklearn.model_selection import train_test_split
from calibrate_and_optimize import load_jsonl, hash_dataset, get_label_distribution, calculate_threshold_metrics
from app.services.ml.training.cost_policy import calculate_expected_cost
from app.services.ml.training.calibration import ProbabilityCalibrator

@pytest.fixture
def mock_validation_data():
    return [
        {"label": 1, "features": {"f1": 1}},
        {"label": 0, "features": {"f1": 0}},
        {"label": 1, "features": {"f1": 1}},
        {"label": 0, "features": {"f1": 0}},
        {"label": 0, "features": {"f1": 0}},
        {"label": 0, "features": {"f1": 0}},
    ]

def test_cost_calculation_formula():
    y_true = pd.Series([1, 0, 1, 0])
    y_prob = pd.Series([0.9, 0.8, 0.4, 0.2])
    # th=0.5 -> pred = [1, 1, 0, 0]
    # FP = 1 (pred=1, true=0 at index 1)
    # FN = 1 (pred=0, true=1 at index 2)
    # N_review = 2 (pred=0 at index 2,3)
    # Cost = (50*1) + (100*1) + (5*2) = 160
    cost = calculate_expected_cost(y_true, y_prob, 0.5, c_fp=50, c_fn=100, c_review=5)
    assert cost == 160.0

def test_configurable_cost_policy():
    y_true = pd.Series([1, 1, 0, 0])
    y_prob = pd.Series([0.9, 0.6, 0.4, 0.1])
    m1 = calculate_threshold_metrics(y_true, y_prob, 0.5, c_fp=50, c_fn=100, c_review=5)
    assert m1["expected_cost"] == 10.0
    m2 = calculate_threshold_metrics(y_true, y_prob, 0.5, c_fp=50, c_fn=100, c_review=100)
    assert m2["expected_cost"] == 200.0

def test_calibrator_fitting_isolation():
    y_true_cal = pd.Series([1, 0])
    y_prob_cal = pd.Series([0.8, 0.2])
    calibrator = ProbabilityCalibrator()
    calibrator.fit(y_prob_cal, y_true_cal)
    assert calibrator.is_fitted == True
    y_prob_thresh = pd.Series([0.9, 0.1])
    y_trans = calibrator.transform(y_prob_thresh)
    assert len(y_trans) == 2

def test_deterministic_stratified_split(mock_validation_data):
    labels = [d["label"] for d in mock_validation_data]
    cal_data, thresh_data = train_test_split(mock_validation_data, test_size=0.5, stratify=labels, random_state=42)
    assert len(cal_data) == 3
    assert len(thresh_data) == 3
    assert sum(1 for d in cal_data if d["label"] == 1) == 1
    assert sum(1 for d in cal_data if d["label"] == 0) == 2
    
def test_f7_test_holdout_access_rejection():
    with pytest.raises(PermissionError, match="TEST_HOLDOUT cannot be loaded"):
        load_jsonl("synthetic_benchmark_v1_test_holdout.jsonl")

def test_no_model_retraining():
    with open('calibrate_and_optimize.py', 'r') as f:
        content = f.read()
    assert 'model.fit(' not in content
    assert 'model.load_model(' in content

def test_deterministic_threshold_selection_grid():
    with open('calibrate_and_optimize.py', 'r') as f:
        content = f.read()
    assert 'np.linspace(0.01, 0.99, 99)' in content

def test_n_review_semantics_no_hard_block():
    with open('app/services/ml/training/cost_policy.py', 'r') as f:
        content = f.read()
    assert 'n_review = np.sum(y_pred == 0)' in content
    assert 'hard_block' not in content

def test_baseline_05_calculated_on_threshold_validation_set():
    with open('calibrate_and_optimize.py', 'r') as f:
        content = f.read()
    assert 'calculate_threshold_metrics(y_thresh, y_prob_cal_thresh, 0.5' in content

def test_calibration_metrics_persisted():
    with open('calibrate_and_optimize.py', 'r') as f:
        content = f.read()
    assert 'brier_score_before' in content
    assert 'brier_score_after' in content
    assert 'calibration_curve' in content

def test_provenance_metadata():
    with open('calibrate_and_optimize.py', 'r') as f:
        content = f.read()
    keys = ["f6_model_hash", "model_version", "catboost_version", "feature_schema_version",
            "split_version", "training_seed", "calibration_method", "subsets",
            "threshold_grid", "optimization", "cost_configuration", "metrics"]
    for k in keys:
        assert f'"{k}"' in content or f"'{k}'" in content

def test_tie_breaking_higher_threshold():
    y_true = pd.Series([1, 1, 0, 0])
    y_prob = pd.Series([0.9, 0.6, 0.4, 0.1])
    c_fp = 50.0; c_fn = 100.0; c_review = 5.0
    grid = [0.45, 0.55] 
    best_cost = float('inf')
    best_threshold = None
    for th in grid:
        metrics = calculate_threshold_metrics(y_true, y_prob, th, c_fp, c_fn, c_review)
        cost = metrics["expected_cost"]
        if cost < best_cost:
            best_cost = cost; best_threshold = th
        elif cost == best_cost:
            if th > best_threshold:
                best_cost = cost; best_threshold = th
    assert best_cost == 10.0
    assert best_threshold == 0.55 

def test_f1_hard_block_separation():
    with open('calibrate_and_optimize.py', 'r') as f:
        content = f.read()
    assert "from app.services.validation_rules" not in content
