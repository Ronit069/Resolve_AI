import os
import json
import hashlib
import numpy as np
import pandas as pd
from datetime import datetime, timezone
import lightgbm as lgb
import joblib

from sklearn.metrics import precision_score, recall_score, f1_score, average_precision_score, roc_auc_score, confusion_matrix
from app.services.ml.training.cost_policy import calculate_expected_cost
from app.services.ml.training.lightgbm_preprocessor import LightGBMPreprocessor

def hash_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def hash_dataset(data: list) -> str:
    h = hashlib.sha256()
    for row in data:
        h.update(json.dumps(row, sort_keys=True).encode("utf-8"))
    return h.hexdigest()

def load_data(filepath: str):
    if "test_holdout" in filepath.lower():
        raise PermissionError("Access to TEST_HOLDOUT is strictly forbidden.")
        
    data = []
    with open(filepath, "r") as f:
        for line in f:
            if line.strip():
                data.append(json.loads(line))
    return data

def main():
    train_file = "synthetic_benchmark_v1_train.jsonl"
    val_file = "synthetic_benchmark_v1_validation.jsonl"
    
    print("Loading datasets...")
    train_data = load_data(train_file)
    val_data = load_data(val_file)
    
    print("Initializing and fitting LightGBMPreprocessor...")
    preprocessor = LightGBMPreprocessor()
    preprocessor.fit(train_data)
    
    X_train, y_train, train_example_ids = preprocessor.transform(train_data)
    X_val, y_val, val_example_ids = preprocessor.transform(val_data)
    
    # Validation Dataset specifically for Early Stopping inside LightGBM
    lgb_train = lgb.Dataset(X_train, label=y_train)
    lgb_val = lgb.Dataset(X_val, label=y_val, reference=lgb_train)
    
    # Deterministic Hyperparameter Grid Search (8 Candidates)
    num_leaves_opts = [15, 31]
    learning_rate_opts = [0.05, 0.10]
    is_unbalance_opts = [True, False]
    
    best_pr_auc = -1
    best_config = None
    best_model = None
    best_val_preds = None
    
    print("Running deterministic hyperparameter grid...")
    for leaves in num_leaves_opts:
        for lr in learning_rate_opts:
            for unbal in is_unbalance_opts:
                params = {
                    "objective": "binary",
                    "metric": "binary_logloss",
                    "num_leaves": leaves,
                    "learning_rate": lr,
                    "is_unbalance": unbal,
                    "feature_fraction": 0.8,
                    "bagging_fraction": 0.8,
                    "min_child_samples": 20,
                    "max_depth": -1,
                    "random_state": 42,
                    "bagging_seed": 42,
                    "feature_fraction_seed": 42,
                    "verbose": -1
                }
                
                # We use callbacks for early stopping
                # n_estimators=1000 is equivalent to num_boost_round=1000
                model = lgb.train(
                    params,
                    lgb_train,
                    num_boost_round=1000,
                    valid_sets=[lgb_val],
                    callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)]
                )
                
                # Predict uses best_iteration automatically if early stopping was met
                preds = model.predict(X_val)
                pr_auc = average_precision_score(y_val, preds)
                
                if pr_auc > best_pr_auc:
                    best_pr_auc = pr_auc
                    best_config = params
                    best_model = model
                    best_val_preds = preds

    print(f"Selected Config: num_leaves={best_config['num_leaves']}, lr={best_config['learning_rate']}, is_unbalance={best_config['is_unbalance']}")
    print(f"Best PR-AUC (Validation Selection): {best_pr_auc:.4f}")
    
    # Calculate External Metrics for Reporting (Threshold 0.5 Diagnostic)
    y_val_pred_class = (best_val_preds >= 0.5).astype(int)
    
    precision = precision_score(y_val, y_val_pred_class, zero_division=0)
    recall = recall_score(y_val, y_val_pred_class, zero_division=0)
    f1 = f1_score(y_val, y_val_pred_class, zero_division=0)
    roc_auc = roc_auc_score(y_val, best_val_preds)
    tn, fp, fn, tp = confusion_matrix(y_val, y_val_pred_class).ravel()
    
    # Cost Policy Diagnostic
    c_fp = float(os.environ.get("C_FP", 50.0))
    c_fn = float(os.environ.get("C_FN", 100.0))
    c_review = float(os.environ.get("C_REVIEW", 5.0))
    
    cost = calculate_expected_cost(
        pd.Series(y_val), 
        pd.Series(best_val_preds), 
        threshold=0.5, 
        c_fp=c_fp, c_fn=c_fn, c_review=c_review
    )
    
    metrics = {
        "precision": float(precision),
        "recall": float(recall),
        "f1_score": float(f1),
        "pr_auc": float(best_pr_auc),
        "roc_auc": float(roc_auc),
        "confusion_matrix": {
            "TP": int(tp), "TN": int(tn), "FP": int(fp), "FN": int(fn)
        },
        "diagnostic_cost_at_0.5": cost
    }
    
    # Artifact Output
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join("artifacts", f"lightgbm_comparator_{timestamp}_v1")
    os.makedirs(out_dir, exist_ok=True)
    
    # 1. Validation Probabilities (Exactly: example_id, p_safe_to_contest, true_label)
    prob_df = pd.DataFrame({
        "example_id": val_example_ids,
        "p_safe_to_contest": best_val_preds,
        "true_label": y_val
    })
    prob_df.to_csv(os.path.join(out_dir, "validation_probabilities.csv"), index=False)
    
    # 2. LightGBM Model Snapshot
    model_path = os.path.join(out_dir, "lightgbm_model.txt")
    best_model.save_model(model_path)
    model_hash = hash_file(model_path)
    
    # 3. Preprocessor (Categorical Vocabularies)
    preprocessor_path = os.path.join(out_dir, "lightgbm_preprocessor.joblib")
    joblib.dump(preprocessor, preprocessor_path)
    
    # 4. Metadata
    meta = {
        "model_type": "LightGBM Strong Comparator",
        "blueprint_step": 11,
        "training_seed": 42,
        "selected_hyperparameters": best_config,
        "best_iteration": best_model.best_iteration,
        "validation_after_model_selection": metrics,
        "provenance": {
            "train_hash": hash_dataset(train_data),
            "validation_hash": hash_dataset(val_data),
            "lightgbm_version": lgb.__version__
        },
        "cost_configuration": {
            "c_fp": c_fp, "c_fn": c_fn, "c_review": c_review
        },
        "artifacts": {
            "model_txt_sha256": model_hash
        }
    }
    
    with open(os.path.join(out_dir, "metadata_lightgbm.json"), "w") as f:
        json.dump(meta, f, indent=2)
        
    print(f"Done. Artifacts exported to {out_dir}")

if __name__ == "__main__":
    main()
