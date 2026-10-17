import os
import sys
import json
import hashlib
import time
import datetime
import catboost
from typing import List, Dict, Any

from app.services.ml.training.preprocessor import Preprocessor
from app.services.ml.training.catboost_trainer import CatBoostTrainer
from app.services.ml.dataset_splitter import FORBIDDEN_FEATURES

def hash_file(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        buf = f.read()
        hasher.update(buf)
    return hasher.hexdigest()

def load_jsonl(filepath: str) -> List[Dict[str, Any]]:
    if "test_holdout" in filepath.lower():
        raise PermissionError(f"TEST_HOLDOUT cannot be loaded by the training pipeline: {filepath}")
    
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
    print("Loading TRAIN and VALIDATION sets...")
    
    train_file = "synthetic_benchmark_v1_train.jsonl"
    val_file = "synthetic_benchmark_v1_validation.jsonl"
    
    try:
        train_data = load_jsonl(train_file)
        val_data = load_jsonl(val_file)
    except PermissionError as e:
        print(f"FATAL ERROR: {e}")
        return
        
    print(f"Loaded {len(train_data)} train examples.")
    print(f"Loaded {len(val_data)} validation examples.")
    
    preprocessor = Preprocessor()
    
    print("Fitting preprocessor on TRAIN...")
    preprocessor.fit(train_data)
    
    print("Transforming datasets...")
    X_train, y_train = preprocessor.transform(train_data)
    X_val, y_val = preprocessor.transform(val_data)
    
    trainer = CatBoostTrainer(categorical_features=preprocessor.categorical_features)
    
    print("Training CatBoost model...")
    trainer.train(X_train, y_train, X_val, y_val)
    
    print("Evaluating model...")
    c_fp = float(os.environ.get("C_FP", "50.0"))
    c_fn = float(os.environ.get("C_FN", "100.0"))
    c_review = float(os.environ.get("C_REVIEW", "5.0"))
    metrics = trainer.evaluate(X_val, y_val, c_fp=c_fp, c_fn=c_fn, c_review=c_review)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = f"artifacts/runs/{timestamp}_v1"
    os.makedirs(run_dir, exist_ok=True)
    
    model_path = os.path.join(run_dir, "model.cbm")
    trainer.save(model_path)
    
    # Calculate hashes
    train_sha = hash_file(train_file)
    val_sha = hash_file(val_file)
    model_sha = hash_file(model_path)
    
    metadata = {
        "model_version": f"catboost_v1_{timestamp}",
        "algorithm": "CatBoost",
        "catboost_version": catboost.__version__,
        "python_version": sys.version,
        "platform": sys.platform,
        "feature_schema_version": "ml_features_v1",
        "split_version": "split_v1",
        "training_seed": trainer.seed,
        "best_iteration": trainer.best_iteration,
        "hyperparameters": {
            "iterations": 1000,
            "auto_class_weights": "Balanced",
            "eval_metric": "PRAUC",
            "early_stopping_rounds": 50,
            "nan_mode": "Min"
        },
        "datasets": {
            "train_sha256": train_sha,
            "train_count": len(train_data),
            "train_distribution": {
                "SAFE_TO_CONTEST": sum(1 for d in train_data if d["label"] == 1),
                "NOT_SAFE_TO_AUTOMATE": sum(1 for d in train_data if d["label"] == 0)
            },
            "validation_sha256": val_sha,
            "validation_count": len(val_data)
        },
        "artifacts": {
            "model_cbm_sha256": model_sha
        },
        "cost_configuration": {
            "c_fp": c_fp,
            "c_fn": c_fn,
            "c_review": c_review
        },
        "validation_metrics": metrics,
        "hard_block_policy": "F1 deterministic policy applies downstream of this model",
        "timestamp": timestamp
    }
    
    meta_path = os.path.join(run_dir, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    # Module L, L-01/L-02/L-03: best-effort MLflow tracking (issue #15).
    # train_lightgbm_comparator.py has logged its runs here since Module L
    # landed, but this script -- the one that actually produces what
    # promote_champion.py promotes -- never did, so MLflow only ever had a
    # record of the comparator model, never the real champion. Tagging
    # model_artifact_sha256 with the exact same model_cbm_sha256 this run's
    # metadata.json (and, on promotion, ModelVersion.preprocessing_hash)
    # already uses is what makes an MLflow run, a promoted ModelVersion row,
    # and the object-storage artifact (champion_loader.py) the same "what
    # model is where" by a single lookup key, not three disconnected ones.
    from app.services.mlops.mlflow_tracking import log_training_run

    log_training_run(
        experiment_name="resolveai_risk_model",
        run_name=os.path.basename(run_dir),
        params={
            "model_type": metadata["algorithm"],
            "training_seed": metadata["training_seed"],
            **{f"hp_{k}": v for k, v in metadata["hyperparameters"].items()},
            **{f"cost_{k}": v for k, v in metadata["cost_configuration"].items()},
        },
        metrics=metrics,
        tags={
            "algorithm": "catboost",
            "train_dataset_hash": train_sha,
            "validation_dataset_hash": val_sha,
            "catboost_version": catboost.__version__,
            "model_artifact_sha256": model_sha,
        },
        artifact_dir=run_dir,
    )

    print(f"\n================== TRAINING COMPLETE ==================")
    print(f"Run Directory: {run_dir}")
    print(f"Model Hashes: {model_sha}")
    print("Metrics:")
    for k, v in metrics.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")
            
    print("SUCCESS: F6 Training constraints met.")

if __name__ == "__main__":
    main()
