import os
import sys
import json
import hashlib
import time
import datetime
import glob
import catboost
import numpy as np
import pandas as pd
import joblib
from typing import List, Dict, Any
from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix, precision_score, recall_score, f1_score, roc_auc_score, average_precision_score

from app.services.ml.training.preprocessor import Preprocessor
from app.services.ml.training.cost_policy import calculate_expected_cost
from app.services.ml.training.calibration import ProbabilityCalibrator, calculate_brier_score, calculate_calibration_curve
from app.services.ml.dataset_splitter import FORBIDDEN_FEATURES

def hash_file(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        buf = f.read()
        hasher.update(buf)
    return hasher.hexdigest()

def get_latest_f6_run_dir() -> str:
    dirs = glob.glob("artifacts/runs/*_v1")
    if not dirs:
        raise RuntimeError("No F6 run directory found in artifacts/runs/")
    return sorted(dirs)[-1]

def load_jsonl(filepath: str) -> List[Dict[str, Any]]:
    if "test_holdout" in filepath.lower():
        raise PermissionError(f"TEST_HOLDOUT cannot be loaded by the calibration pipeline: {filepath}")
    
    data = []
    with open(filepath, 'r') as f:
        for line in f:
            ex = json.loads(line)
            # Leakage Check
            for forbidden in FORBIDDEN_FEATURES:
                if forbidden in ex.get("features", {}):
                    raise ValueError(f"Forbidden feature {forbidden} detected in payload!")
            data.append(ex)
    return data

def hash_dataset(data: List[Dict[str, Any]]) -> str:
    hasher = hashlib.sha256()
    for ex in data:
        hasher.update(json.dumps(ex, sort_keys=True).encode("utf-8"))
    return hasher.hexdigest()

def get_label_distribution(data: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "SAFE_TO_CONTEST": sum(1 for d in data if d["label"] == 1),
        "NOT_SAFE_TO_AUTOMATE": sum(1 for d in data if d["label"] == 0)
    }

def calculate_threshold_metrics(y_true: pd.Series, y_prob: pd.Series, threshold: float, c_fp: float, c_fn: float, c_review: float) -> Dict[str, float]:
    y_pred = (y_prob >= threshold).astype(int)
    cm = confusion_matrix(y_true, y_pred)
    if cm.shape == (1, 1):
        if y_true.iloc[0] == 0:
            cm = np.array([[cm[0,0], 0], [0, 0]])
        else:
            cm = np.array([[0, 0], [0, cm[0,0]]])
            
    tn = cm[0,0] if cm.shape == (2,2) else 0
    fp = cm[0,1] if cm.shape == (2,2) else 0
    fn = cm[1,0] if cm.shape == (2,2) else 0
    tp = cm[1,1] if cm.shape == (2,2) else 0
    
    precision = float(precision_score(y_true, y_pred, zero_division=0))
    recall = float(recall_score(y_true, y_pred, zero_division=0))
    f1 = float(f1_score(y_true, y_pred, zero_division=0))
    
    cost = calculate_expected_cost(y_true, y_prob, threshold=threshold, c_fp=c_fp, c_fn=c_fn, c_review=c_review)
    
    return {
        "threshold": threshold,
        "tp": int(tp),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "expected_cost": cost
    }

def main():
    val_file = "synthetic_benchmark_v1_validation.jsonl"
    
    try:
        val_data = load_jsonl(val_file)
    except PermissionError as e:
        print(f"FATAL ERROR: {e}")
        return
        
    print(f"Loaded {len(val_data)} validation examples.")
    
    # 2. Deterministic Stratified Split
    # We need just the labels for stratification
    labels = [d["label"] for d in val_data]
    
    cal_data, thresh_data = train_test_split(val_data, test_size=0.5, stratify=labels, random_state=42)
    print(f"Split into CALIBRATION_SET ({len(cal_data)}) and THRESHOLD_VALIDATION_SET ({len(thresh_data)})")
    
    cal_dist = get_label_distribution(cal_data)
    thresh_dist = get_label_distribution(thresh_data)
    
    # 3. Load F6 Model
    f6_run_dir = get_latest_f6_run_dir()
    model_path = os.path.join(f6_run_dir, "model.cbm")
    meta_path = os.path.join(f6_run_dir, "metadata.json")
    
    print(f"Loading F6 model from {model_path}")
    model = catboost.CatBoostClassifier()
    model.load_model(model_path)
    
    with open(meta_path, "r") as f:
        f6_meta = json.load(f)
        
    f6_model_hash = hash_file(model_path)
    
    # 4. Extract features
    preprocessor = Preprocessor()
    preprocessor.is_fitted = True # Do not refit, it has no state anyway
    
    X_cal, y_cal = preprocessor.transform(cal_data)
    X_thresh, y_thresh = preprocessor.transform(thresh_data)
    
    # 5. Predict uncalibrated probabilities using frozen F6 model
    y_prob_uncal_cal = pd.Series(model.predict_proba(X_cal)[:, 1])
    y_prob_uncal_thresh = pd.Series(model.predict_proba(X_thresh)[:, 1])
    
    # 6. Fit Calibrator
    print("Fitting IsotonicRegression calibrator on CALIBRATION_SET...")
    calibrator = ProbabilityCalibrator()
    calibrator.fit(y_prob_uncal_cal, y_cal)
    
    # 7. Evaluate Calibrator on THRESHOLD_VALIDATION_SET
    print("Evaluating calibrator on THRESHOLD_VALIDATION_SET...")
    y_prob_cal_thresh = calibrator.transform(y_prob_uncal_thresh)
    
    brier_before = calculate_brier_score(y_thresh, y_prob_uncal_thresh)
    brier_after = calculate_brier_score(y_thresh, y_prob_cal_thresh)
    cal_curve = calculate_calibration_curve(y_thresh, y_prob_cal_thresh)
    
    # Ranking metrics (Threshold-independent)
    pr_auc = float(average_precision_score(y_thresh, y_prob_cal_thresh))
    roc_auc = float(roc_auc_score(y_thresh, y_prob_cal_thresh))
    
    # 8. Threshold Optimization
    print("Optimizing threshold...")
    c_fp = float(os.environ.get("C_FP", "50.0"))
    c_fn = float(os.environ.get("C_FN", "100.0"))
    c_review = float(os.environ.get("C_REVIEW", "5.0"))
    
    grid = np.linspace(0.01, 0.99, 99).tolist()
    
    best_threshold = None
    best_cost = float('inf')
    best_metrics = None
    
    for th in grid:
        metrics = calculate_threshold_metrics(y_thresh, y_prob_cal_thresh, th, c_fp, c_fn, c_review)
        cost = metrics["expected_cost"]
        
        # Tie-breaking: If multiple thresholds have exactly equal minimum cost, select the HIGHER threshold.
        if cost < best_cost:
            best_cost = cost
            best_threshold = th
            best_metrics = metrics
        elif cost == best_cost:
            if th > best_threshold:
                best_cost = cost
                best_threshold = th
                best_metrics = metrics
                
    # Baseline 0.5
    baseline_metrics = calculate_threshold_metrics(y_thresh, y_prob_cal_thresh, 0.5, c_fp, c_fn, c_review)
    
    # 9. Artifact Creation
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = f"artifacts/calibration_{timestamp}_v1"
    os.makedirs(run_dir, exist_ok=True)
    
    cal_path = os.path.join(run_dir, "calibrator.joblib")
    joblib.dump(calibrator.calibrator, cal_path)
    cal_hash = hash_file(cal_path)
    
    metadata = {
        "f6_model_hash": f6_model_hash,
        "model_version": f6_meta["model_version"],
        "catboost_version": catboost.__version__,
        "feature_schema_version": "ml_features_v1",
        "split_version": "split_v1",
        "training_seed": 42,
        "calibration_method": "IsotonicRegression",
        "subsets": {
            "calibration_set": {
                "size": len(cal_data),
                "distribution": cal_dist,
                "hash": hash_dataset(cal_data)
            },
            "threshold_validation_set": {
                "size": len(thresh_data),
                "distribution": thresh_dist,
                "hash": hash_dataset(thresh_data)
            }
        },
        "threshold_grid": {
            "min": 0.01,
            "max": 0.99,
            "steps": 99,
            "values": grid
        },
        "optimization": {
            "selected_threshold": best_threshold,
            "tie_breaking_rule": "If multiple thresholds have exactly equal minimum ExpectedCost, select the HIGHEST threshold."
        },
        "cost_configuration": {
            "c_fp": c_fp,
            "c_fn": c_fn,
            "c_review": c_review
        },
        "metrics": {
            "ranking_metrics_independent": {
                "brier_score_before": brier_before,
                "brier_score_after": brier_after,
                "pr_auc": pr_auc,
                "roc_auc": roc_auc,
                "calibration_curve": cal_curve
            },
            "threshold_selection_diagnostics_not_final": {
                "baseline_0.5": baseline_metrics,
                "optimized_threshold": best_metrics
            }
        },
        "artifacts": {
            "calibrator_joblib_sha256": cal_hash
        },
        "timestamp": timestamp
    }
    
    meta_path = os.path.join(run_dir, "metadata_calibration.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
        
    print(f"\n================== CALIBRATION & OPTIMIZATION COMPLETE ==================")
    print(f"Run Directory: {run_dir}")
    print(f"Brier Before: {brier_before:.4f} -> After: {brier_after:.4f}")
    print(f"Baseline Cost (0.5): {baseline_metrics['expected_cost']:.2f}")
    print(f"Optimized Cost ({best_threshold:.2f}): {best_metrics['expected_cost']:.2f}")
    print("SUCCESS: F7 Calibration constraints met.")

if __name__ == "__main__":
    main()
