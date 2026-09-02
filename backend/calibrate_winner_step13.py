import os
import sys
import json
import hashlib
import datetime
import pickle
import pandas as pd
import numpy as np
import catboost
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss
from sklearn.calibration import calibration_curve
from sklearn.model_selection import train_test_split

# Add the parent directory to sys.path so we can import app modules if running from script
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ml.dataset_splitter import FORBIDDEN_FEATURES
from app.services.ml.training.preprocessor import Preprocessor

def hash_file(filepath: str) -> str:
    if "test_holdout" in filepath.lower():
        raise PermissionError("Access to TEST_HOLDOUT is strictly forbidden.")
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        buf = f.read()
        hasher.update(buf)
    return hasher.hexdigest()

def get_latest_dir(parent_dir, match_str, exclude_str=None):
    dirs = [d for d in os.listdir(parent_dir) if match_str in d]
    if exclude_str:
        dirs = [d for d in dirs if exclude_str not in d]
    if not dirs:
        raise ValueError(f"No directory matching {match_str} found in {parent_dir}")
    return sorted(dirs)[-1]

def load_jsonl(filepath: str):
    if "test_holdout" in filepath.lower():
        raise PermissionError(f"TEST_HOLDOUT cannot be loaded by the pipeline: {filepath}")
    
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

def main():
    print("Executing Step 13: Calibration of Winning Model (CatBoost)...")

    # 1. Load the frozen validation set
    val_file = "synthetic_benchmark_v1_validation.jsonl"
    val_data = load_jsonl(val_file)
    val_sha = hash_file(val_file)

    # 2. Get CatBoost latest model
    artifacts_dir = "artifacts"
    f6_dir = get_latest_dir(os.path.join(artifacts_dir, "runs"), "", exclude_str="calibrated")
    model_path = os.path.join(artifacts_dir, "runs", f6_dir, "model.cbm")
    meta_path = os.path.join(artifacts_dir, "runs", f6_dir, "metadata.json")

    model_sha = hash_file(model_path)
    
    # 3. Load CatBoost
    model = catboost.CatBoostClassifier()
    model.load_model(model_path)

    # 4. Extract features
    preprocessor = Preprocessor()
    preprocessor.fit(val_data) # fitting doesn't change state but is required API
    X_val, y_val = preprocessor.transform(val_data)
    
    # 5. Raw predictions
    # DO NOT CALL model.fit() here
    raw_probs = model.predict_proba(X_val)[:, 1]

    # Assemble dataframe for split
    df = pd.DataFrame({
        "example_id": [ex["example_id"] for ex in val_data],
        "true_label": y_val,
        "raw_p_safe_to_contest": raw_probs
    })

    # 6. Stratified 50/50 Split (Engineering Decision)
    fit_df, eval_df = train_test_split(
        df, 
        test_size=0.5, 
        random_state=42, 
        stratify=df["true_label"]
    )

    fit_df = fit_df.copy()
    eval_df = eval_df.copy()

    # 7. Fit Calibrator ONLY on fit_df
    calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    calibrator.fit(fit_df["raw_p_safe_to_contest"], fit_df["true_label"])

    # 8. Transform BOTH sets
    fit_df["calibrated_p_safe_to_contest"] = calibrator.predict(fit_df["raw_p_safe_to_contest"])
    eval_df["calibrated_p_safe_to_contest"] = calibrator.predict(eval_df["raw_p_safe_to_contest"])

    # Clip explicitly just in case
    eval_df["calibrated_p_safe_to_contest"] = eval_df["calibrated_p_safe_to_contest"].clip(0.0, 1.0)
    fit_df["calibrated_p_safe_to_contest"] = fit_df["calibrated_p_safe_to_contest"].clip(0.0, 1.0)

    # 9. Evaluate ONLY on eval_df
    brier_before = float(brier_score_loss(eval_df["true_label"], eval_df["raw_p_safe_to_contest"]))
    brier_after = float(brier_score_loss(eval_df["true_label"], eval_df["calibrated_p_safe_to_contest"]))

    prob_true, prob_pred = calibration_curve(eval_df["true_label"], eval_df["calibrated_p_safe_to_contest"], n_bins=10)
    
    # 10. Generate artifacts
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = f"artifacts/step13_calibration_{timestamp}_v1"
    os.makedirs(out_dir, exist_ok=True)

    # Save calibrator
    calibrator_path = os.path.join(out_dir, "calibrator.pkl")
    with open(calibrator_path, "wb") as f:
        pickle.dump(calibrator, f)

    # Save combined csv (both fit and eval so downstream can use it if needed)
    full_df = pd.concat([fit_df, eval_df]).sort_values("example_id")
    csv_path = os.path.join(out_dir, "calibrated_validation_probabilities.csv")
    full_df.to_csv(csv_path, index=False)

    # Subset hashes for provenance
    fit_subset_ids = sorted(fit_df["example_id"].tolist())
    eval_subset_ids = sorted(eval_df["example_id"].tolist())
    fit_subset_hash = hashlib.sha256(json.dumps(fit_subset_ids).encode()).hexdigest()
    eval_subset_hash = hashlib.sha256(json.dumps(eval_subset_ids).encode()).hexdigest()

    provenance = {
        "winner": "CatBoost",
        "winner_selection": "manual_step12_resolution",
        "calibration_method": "IsotonicRegression",
        "isotonic_configuration": {
            "y_min": 0.0,
            "y_max": 1.0,
            "out_of_bounds": "clip"
        },
        "random_seed": 42,
        "split_method": "stratified 50/50 split",
        "source_validation_dataset_hash": val_sha,
        "source_model_cbm_hash": model_sha,
        "calibration_fit_subset_hash": fit_subset_hash,
        "calibration_evaluation_subset_hash": eval_subset_hash,
        "observation_counts": {
            "fit": len(fit_df),
            "evaluation": len(eval_df),
            "total": len(full_df)
        },
        "metrics": {
            "brier_score_before": brier_before,
            "brier_score_after": brier_after,
            "calibration_curve": {
                "prob_true": prob_true.tolist(),
                "prob_pred": prob_pred.tolist()
            }
        },
        "timestamp": timestamp,
        "python_version": os.sys.version,
        "test_holdout_hash": "NOT COMPUTED"
    }

    metrics_path = os.path.join(out_dir, "calibration_metrics.json")
    with open(metrics_path, "w") as f:
        json.dump(provenance, f, indent=2)

    print(f"Step 13 complete. Calibrator and metrics saved to {out_dir}")

if __name__ == "__main__":
    main()
