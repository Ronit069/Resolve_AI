import os
import json
import pandas as pd
from datetime import datetime, timezone
import hashlib
import math

from sklearn.metrics import precision_score, recall_score, f1_score, average_precision_score, roc_auc_score, confusion_matrix
from app.services.ml.training.cost_policy import calculate_expected_cost

def hash_file(filepath: str) -> str:
    if "test_holdout" in filepath.lower():
        raise PermissionError("Access to TEST_HOLDOUT is strictly forbidden.")
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def get_latest_dir(parent_dir, match_str, exclude_str=None):
    dirs = [d for d in os.listdir(parent_dir) if match_str in d]
    if exclude_str:
        dirs = [d for d in dirs if exclude_str not in d]
    if not dirs:
        raise ValueError(f"No directory matching {match_str} found in {parent_dir}")
    return sorted(dirs)[-1]

def evaluate_ml_candidate_from_csv(csv_path: str):
    if "test_holdout" in csv_path.lower():
        raise PermissionError("Access to TEST_HOLDOUT is strictly forbidden.")
        
    df = pd.read_csv(csv_path)
    df = df.sort_values(by="example_id").reset_index(drop=True)
    
    y_true = df["true_label"].astype(int)
    y_prob = df["p_safe_to_contest"]
    
    y_pred = (y_prob >= 0.5).astype(int)
    
    pr_auc = average_precision_score(y_true, y_prob)
    roc_auc = roc_auc_score(y_true, y_prob)
    precision = precision_score(y_true, y_pred, zero_division=0)
    recall = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
    
    cost = calculate_expected_cost(
        y_true,
        y_prob,
        threshold=0.5,
        c_fp=50.0,
        c_fn=100.0,
        c_review=5.0
    )
    
    return {
        "example_ids": df["example_id"].tolist(),
        "metrics": {
            "PR-AUC": float(pr_auc),
            "ROC-AUC": float(roc_auc),
            "Precision": float(precision),
            "Recall": float(recall),
            "F1": float(f1),
            "Expected Cost": float(cost),
            "Status": "ML Candidate",
            "Confusion Matrix": {"TP": int(tp), "TN": int(tn), "FP": int(fp), "FN": int(fn)}
        }
    }

def get_ml_candidate_from_metadata(meta_path: str):
    with open(meta_path, "r") as f:
        meta = json.load(f)
        
    vm = meta.get("validation_metrics", {})
    return {
        "example_ids": None, # Will verify by dataset hash instead
        "dataset_hash": meta.get("datasets", {}).get("validation_sha256"),
        "metrics": {
            "PR-AUC": vm.get("pr_auc"),
            "ROC-AUC": vm.get("roc_auc"),
            "Precision": vm.get("precision"),
            "Recall": vm.get("recall"),
            "F1": vm.get("f1"),
            "Expected Cost": vm.get("expected_cost_diagnostic"),
            "Status": "ML Candidate",
            "Confusion Matrix": {
                "TP": vm.get("confusion_matrix", {}).get("tp"),
                "TN": vm.get("confusion_matrix", {}).get("tn"),
                "FP": vm.get("confusion_matrix", {}).get("fp"),
                "FN": vm.get("confusion_matrix", {}).get("fn")
            }
        }
    }

def main():
    artifacts_dir = "artifacts"
    
    # 1. Baseline 0 (Module E)
    f8_dir = get_latest_dir(artifacts_dir, "baseline_module_e")
    f8_meta_path = os.path.join(artifacts_dir, f8_dir, "baseline_metrics.json")
    with open(f8_meta_path, "r") as f:
        f8_meta = json.load(f)
        
    baseline_0 = {
        "PR-AUC": "N/A",
        "ROC-AUC": "N/A",
        "Precision": f8_meta["metrics"]["classification"]["precision"],
        "Recall": f8_meta["metrics"]["classification"]["recall"],
        "F1": f8_meta["metrics"]["classification"]["f1_score"],
        "Expected Cost": f8_meta["metrics"]["business_cost"]["expected_cost"],
        "Status": "Deterministic Module E rule output",
        "Confusion Matrix": f8_meta["metrics"]["confusion_matrix"]
    }
    
    # 2. Baseline 1 (Logistic Regression)
    f9_dir = get_latest_dir(artifacts_dir, "logistic_baseline")
    f9_csv = os.path.join(artifacts_dir, f9_dir, "validation_probabilities.csv")
    f9_res = evaluate_ml_candidate_from_csv(f9_csv)
    baseline_1 = f9_res["metrics"]
    baseline_1["Status"] = "ML Candidate"
    
    # 3. Candidate 1 (CatBoost F6)
    # F6 doesn't output CSV, so we use its metadata
    f6_dir = get_latest_dir(os.path.join(artifacts_dir, "runs"), "", exclude_str="calibrated")
    f6_meta_path = os.path.join(artifacts_dir, "runs", f6_dir, "metadata.json")
    f6_res = get_ml_candidate_from_metadata(f6_meta_path)
    candidate_1 = f6_res["metrics"]
    
    # 4. Candidate 2 (LightGBM F11)
    f11_dir = get_latest_dir(artifacts_dir, "lightgbm_comparator")
    f11_csv = os.path.join(artifacts_dir, f11_dir, "validation_probabilities.csv")
    f11_res = evaluate_ml_candidate_from_csv(f11_csv)
    candidate_2 = f11_res["metrics"]
    
    # Validation dataset parity
    if f9_res["example_ids"] != f11_res["example_ids"]:
        raise ValueError("Validation example_ids mismatch between Logistic Regression and LightGBM.")
        
    val_hash = f8_meta["provenance"]["dataset_hash"]
    if f6_res["dataset_hash"] != val_hash:
        raise ValueError(f"F6 Validation dataset hash mismatch. F8 uses {val_hash}, F6 uses {f6_res['dataset_hash']}")
    
    ml_candidates = {
        "Logistic Regression": baseline_1,
        "CatBoost": candidate_1,
        "LightGBM": candidate_2
    }
    
    best_pr_auc = -1.0
    winner = None
    tie = False
    
    for name, metrics in ml_candidates.items():
        pr = float(metrics["PR-AUC"])
        
        if best_pr_auc < 0:
            best_pr_auc = pr
            winner = name
            tie = False
            continue
            
        if math.isclose(pr, best_pr_auc, rel_tol=1e-9, abs_tol=1e-12):
            tie = True
        elif pr > best_pr_auc:
            best_pr_auc = pr
            winner = name
            tie = False
            
    if tie:
        raise RuntimeError("Blueprint Ambiguity: PR-AUC is mathematically tied. Manual review required.")
        
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "blueprint_step": 12,
        "winner_selection": {
            "selected_model": winner,
            "primary_metric": "PR-AUC",
            "best_score": best_pr_auc
        },
        "model_comparison_table": {
            "Module E Rules": baseline_0,
            "Logistic Regression": baseline_1,
            "CatBoost": candidate_1,
            "LightGBM": candidate_2
        },
        "provenance": {
            "validation_dataset_hash": val_hash,
            "f8_baseline_metrics_hash": hash_file(f8_meta_path),
            "f9_validation_csv_hash": hash_file(f9_csv),
            "f6_metadata_hash": hash_file(f6_meta_path),
            "f11_validation_csv_hash": hash_file(f11_csv)
        }
    }
    
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(artifacts_dir, f"step12_model_comparison_{timestamp_str}")
    os.makedirs(out_dir, exist_ok=True)
    
    out_path = os.path.join(out_dir, "comparison_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
        
    print(f"Step 12 complete. Winner: {winner}")
    print(f"Report saved to {out_path}")

if __name__ == "__main__":
    main()
