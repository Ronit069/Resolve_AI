import os
import sys
import json
import hashlib
import datetime
import pandas as pd
import numpy as np

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

def calculate_3way_cost(df: pd.DataFrame, t_accept: float, t_contest: float, c_fp: float, c_fn: float, c_review: float):
    # p < T_accept -> ACCEPT
    # T_accept <= p < T_contest -> REVIEW
    # p >= T_contest -> CONTEST
    
    # FP: p >= T_contest AND true_label == 0
    fp_mask = (df["calibrated_p_safe_to_contest"] >= t_contest) & (df["true_label"] == 0)
    
    # FN: p < T_accept AND true_label == 1
    fn_mask = (df["calibrated_p_safe_to_contest"] < t_accept) & (df["true_label"] == 1)
    
    # N_review: T_accept <= p < T_contest
    review_mask = (df["calibrated_p_safe_to_contest"] >= t_accept) & (df["calibrated_p_safe_to_contest"] < t_contest)
    
    fp_count = fp_mask.sum()
    fn_count = fn_mask.sum()
    n_review = review_mask.sum()
    
    accept_count = (df["calibrated_p_safe_to_contest"] < t_accept).sum()
    contest_count = (df["calibrated_p_safe_to_contest"] >= t_contest).sum()
    
    # True positive for precision/recall (positive class = CONTEST)
    tp_mask = (df["calibrated_p_safe_to_contest"] >= t_contest) & (df["true_label"] == 1)
    tp_count = tp_mask.sum()
    
    # Precision: TP / (TP + FP) -> Note that 'contest_count' = TP + FP
    precision = tp_count / contest_count if contest_count > 0 else 0.0
    
    # FN for standard binary classification recall relative to CONTEST:
    # A true_label==1 case is a False Negative if it was NOT routed to CONTEST.
    # Therefore, FN = cases where (calibrated_p < t_contest) AND (true_label == 1)
    fn_metric_mask = (df["calibrated_p_safe_to_contest"] < t_contest) & (df["true_label"] == 1)
    fn_metric_count = fn_metric_mask.sum()
    
    # Recall base is TP + FN (which equals all true_label == 1 cases in the dataset)
    p_true = tp_count + fn_metric_count
    recall = tp_count / p_true if p_true > 0 else 0.0
    
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    # Note: Expected cost specifically relies on the 3-way FN logic (where FN_cost is only 
    # applied to cases that auto-accept when they shouldn't). This is unchanged.
    expected_cost = (c_fp * fp_count) + (c_fn * fn_count) + (c_review * n_review)
    
    return {
        "expected_cost": float(expected_cost),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "tp_count": int(tp_count),
        "tn_count": int((df["true_label"] == 0).sum() - fp_count),
        "fp_count": int(fp_count),
        "fn_count": int(fn_metric_count),  # Return the standard metric FN, not the cost FN
        "accept_count": int(accept_count),
        "review_count": int(n_review),
        "contest_count": int(contest_count)
    }

def main():
    print("Executing Step 14: Threshold Optimization...")

    artifacts_dir = "artifacts"
    
    # Locate F13 Calibration artifact
    f13_dir = get_latest_dir(artifacts_dir, "step13_calibration")
    csv_path = os.path.join(artifacts_dir, f13_dir, "calibrated_validation_probabilities.csv")
    csv_hash = hash_file(csv_path)
    
    df = pd.read_csv(csv_path)
    if "test_holdout" in csv_path.lower():
        raise PermissionError("Access to TEST_HOLDOUT is strictly forbidden.")
        
    val_file = "synthetic_benchmark_v1_validation.jsonl"
    val_sha = hash_file(val_file)
    
    # Build unique probability grid
    unique_probs = set(df["calibrated_p_safe_to_contest"].unique())
    unique_probs.add(0.0)
    unique_probs.add(1.0)
    
    candidate_thresholds = sorted(list(unique_probs))
    
    c_fp = 50.0
    c_fn = 100.0
    c_review = 5.0
    
    results = []
    
    # Exhaustive pairwise evaluation
    for t_accept in candidate_thresholds:
        for t_contest in candidate_thresholds:
            if t_accept < t_contest:
                metrics = calculate_3way_cost(df, t_accept, t_contest, c_fp, c_fn, c_review)
                results.append({
                    "t_accept": t_accept,
                    "t_contest": t_contest,
                    **metrics
                })
                
    results_df = pd.DataFrame(results)
    candidate_count = len(results_df)
    
    # Remove artificial 0.80 constraints
    valid_df = results_df.copy()
    
    # Find minimum cost
    min_cost = valid_df["expected_cost"].min()
    min_cost_df = valid_df[valid_df["expected_cost"] == min_cost].copy()
    
    # Tie-breaking
    min_cost_df["review_band"] = min_cost_df["t_contest"] - min_cost_df["t_accept"]
    
    # Sort hierarchy:
    # 1. Highest F1 (descending)
    # 2. Highest Precision (descending)
    # 3. Highest Recall (descending)
    # 4. Narrowest Review Band (ascending)
    # 5. Lexicographically lowest t_accept (ascending)
    # 6. Lexicographically lowest t_contest (ascending)
    
    # Since sort_values needs uniform ascending/descending per column, we can do it directly
    selected = min_cost_df.sort_values(
        by=["f1", "precision", "recall", "review_band", "t_accept", "t_contest"],
        ascending=[False, False, False, True, True, True]
    ).iloc[0]
    
    # Generate Output
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = f"artifacts/step14_threshold_policy_{timestamp}_v1"
    os.makedirs(out_dir, exist_ok=True)
    
    grid_csv_path = os.path.join(out_dir, "threshold_optimization_grid.csv")
    results_df.to_csv(grid_csv_path, index=False)
    
    policy = {
        "t_accept": float(selected["t_accept"]),
        "t_contest": float(selected["t_contest"]),
        "threshold_grid_method": "exhaustive_unique_validation_probabilities_plus_0_and_1",
        "threshold_grid_candidate_count": candidate_count,
        "valid_candidates_count": len(valid_df),
        "minimum_precision": None,
        "minimum_recall": None,
        "constraint_status": "NO_NUMERIC_PRECISION_RECALL_FLOORS",
        "primary_objective": "MINIMIZE_EXPECTED_COST",
        "policy_resolution": "User resolved removal of previously imposed 0.80/0.80 floors after feasibility audit demonstrated incompatibility with frozen F13 probability distribution.",
        "c_fp": c_fp,
        "c_fn": c_fn,
        "c_review": c_review,
        "expected_cost": float(selected["expected_cost"]),
        "precision": float(selected["precision"]),
        "recall": float(selected["recall"]),
        "f1": float(selected["f1"]),
        "accept_count": int(selected["accept_count"]),
        "review_count": int(selected["review_count"]),
        "contest_count": int(selected["contest_count"]),
        "tie_break_policy": "F1 -> Precision -> Recall -> narrowest REVIEW band -> lowest T_accept -> lowest T_contest",
        "source_f13_artifact_hash": csv_hash,
        "validation_provenance_hash": val_sha,
        "test_holdout_hash": "NOT COMPUTED",
        "timestamp": timestamp,
        "python_version": sys.version
    }
    
    policy_path = os.path.join(out_dir, "decision_policy.json")
    with open(policy_path, "w") as f:
        json.dump(policy, f, indent=2)
        
    print(f"Step 14 Threshold Policy Generated in {out_dir}")
    print(f"Candidates Evaluated: {candidate_count}")
    print(f"Selected T_accept: {policy['t_accept']:.6f}")
    print(f"Selected T_contest: {policy['t_contest']:.6f}")
    print(f"Expected Cost: {policy['expected_cost']:.2f}")

if __name__ == "__main__":
    main()
