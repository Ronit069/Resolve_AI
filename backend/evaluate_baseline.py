import os
import json
import hashlib
import argparse
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.shared import *
from app.models.module_a import *
from app.models.module_b import *
from app.models.module_c import *
from app.models.module_d import *
from app.models.module_e import EvidenceValidationRun, EvidenceValidationResult, EValidationResultState

def load_data(filepath: str):
    if "test_holdout" in filepath.lower():
        raise PermissionError("Access to TEST_HOLDOUT is strictly forbidden in Step 8.")
        
    data = []
    with open(filepath, "r") as f:
        for line in f:
            if line.strip():
                data.append(json.loads(line))
    return data

def hash_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def evaluate_baseline(val_filepath: str, db_filepath: str, c_fp: float, c_fn: float, c_review: float, output_dir: str):
    print(f"Loading validation file: {val_filepath}")
    val_data = load_data(val_filepath)
    val_hash = hash_file(val_filepath)
    
    engine = create_engine(f"sqlite:///{db_filepath}")
    Session = sessionmaker(bind=engine)
    session = Session()

    tp = 0
    tn = 0
    fp = 0
    fn = 0
    n_review = 0

    coverage = {
        "PASS": 0,
        "FAIL": 0,
        "WARN": 0,
        "UNKNOWN": 0,
        "NA": 0
    }

    # Verify no ML modules are loaded
    import sys
    if "catboost" in sys.modules:
        raise ImportError("CatBoost module is loaded! ML leakage detected.")

    import uuid
    for record in val_data:
        case_id_str = record["case_id"]
        case_id = uuid.UUID(case_id_str)
        gold_label = record["label"]
        
        run = session.query(EvidenceValidationRun).filter(
            EvidenceValidationRun.case_id == case_id
        ).order_by(EvidenceValidationRun.created_at.desc()).first()
        
        if not run:
            print(f"Warning: No EvidenceValidationRun found for case {case_id}")
            continue
            
        results = session.query(EvidenceValidationResult).filter(
            EvidenceValidationResult.validation_run_id == run.id
        ).all()
        
        if not results:
            print(f"Warning: No EvidenceValidationResult found for case {case_id}")
            continue

        baseline_pred = 1 # Assume SAFE_TO_CONTEST
        
        for res in results:
            state = res.result
            if state.name in coverage:
                coverage[state.name] += 1
                
            if state in [EValidationResultState.FAIL, EValidationResultState.WARN, EValidationResultState.UNKNOWN]:
                baseline_pred = 0 # NOT_SAFE_TO_AUTOMATE

        if baseline_pred == 1 and gold_label == 1:
            tp += 1
        elif baseline_pred == 1 and gold_label == 0:
            fp += 1
        elif baseline_pred == 0 and gold_label == 1:
            fn += 1
        elif baseline_pred == 0 and gold_label == 0:
            tn += 1
            
        if baseline_pred == 0:
            n_review += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    
    expected_cost = (c_fp * fp) + (c_fn * fn) + (c_review * n_review)

    metrics = {
        "confusion_matrix": {
            "TP": tp,
            "TN": tn,
            "FP": fp,
            "FN": fn
        },
        "classification": {
            "precision": precision,
            "recall": recall,
            "f1_score": f1
        },
        "business_cost": {
            "expected_cost": expected_cost,
            "C_FP": c_fp,
            "C_FN": c_fn,
            "C_REVIEW": c_review
        },
        "rule_coverage_counts": coverage,
        "pr_auc": "NOT APPLICABLE",
        "roc_auc": "NOT APPLICABLE"
    }
    
    provenance = {
        "dataset_split_version": "v1",
        "dataset_hash": val_hash,
        "evaluation_timestamp": datetime.now(timezone.utc).isoformat(),
        "random_seed": "NA"
    }

    report = {
        "metrics": metrics,
        "provenance": provenance
    }

    os.makedirs(output_dir, exist_ok=True)
    out_file = os.path.join(output_dir, "baseline_metrics.json")
    with open(out_file, "w") as f:
        json.dump(report, f, indent=2)
        
    print(f"Evaluation complete. Results saved to {out_file}")
    print(f"F1 Score: {f1:.4f}")
    print(f"Expected Cost: ${expected_cost:.2f}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate Module E Baseline 0")
    parser.add_argument("--val_file", type=str, default="synthetic_benchmark_v1_validation.jsonl", help="Path to validation JSONL")
    parser.add_argument("--db_file", type=str, default="benchmark_final.db", help="Path to SQLite benchmark DB")
    parser.add_argument("--c_fp", type=float, default=float(os.environ.get("COST_FP", 50.0)), help="Cost of False Positive")
    parser.add_argument("--c_fn", type=float, default=float(os.environ.get("COST_FN", 100.0)), help="Cost of False Negative")
    parser.add_argument("--c_review", type=float, default=float(os.environ.get("COST_REVIEW", 5.0)), help="Cost of Human Review")
    
    args = parser.parse_args()
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join("artifacts", f"baseline_module_e_{timestamp}")
    
    evaluate_baseline(args.val_file, args.db_file, args.c_fp, args.c_fn, args.c_review, out_dir)
