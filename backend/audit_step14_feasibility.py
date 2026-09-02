import os
import json
import datetime
import pandas as pd
from optimize_thresholds_step14 import get_latest_dir, calculate_3way_cost

def main():
    print("Executing Step 14 Policy Feasibility Review...")

    artifacts_dir = "artifacts"
    f13_dir = get_latest_dir(artifacts_dir, "step13_calibration")
    csv_path = os.path.join(artifacts_dir, f13_dir, "calibrated_validation_probabilities.csv")
    
    df = pd.read_csv(csv_path)
    
    unique_probs = set(df["calibrated_p_safe_to_contest"].unique())
    obs_counts = df["calibrated_p_safe_to_contest"].value_counts().to_dict()
    
    unique_probs.add(0.0)
    unique_probs.add(1.0)
    candidate_thresholds = sorted(list(unique_probs))
    
    c_fp, c_fn, c_review = 50.0, 100.0, 5.0
    results = []
    
    for t_accept in candidate_thresholds:
        for t_contest in candidate_thresholds:
            if t_accept < t_contest:
                metrics = calculate_3way_cost(df, t_accept, t_contest, c_fp, c_fn, c_review)
                valid = metrics["precision"] >= 0.80 and metrics["recall"] >= 0.80
                results.append({
                    "T_accept": t_accept,
                    "T_contest": t_contest,
                    "Precision": metrics["precision"],
                    "Recall": metrics["recall"],
                    "F1": metrics["f1"],
                    "TP": metrics["tp_count"],
                    "TN": metrics["tn_count"],
                    "FP": metrics["fp_count"],
                    "FN": metrics["fn_count"],
                    "ACCEPT_count": metrics["accept_count"],
                    "REVIEW_count": metrics["review_count"],
                    "CONTEST_count": metrics["contest_count"],
                    "ExpectedCost": metrics["expected_cost"],
                    "Precision >= 0.80": metrics["precision"] >= 0.80,
                    "Recall >= 0.80": metrics["recall"] >= 0.80,
                    "Overall_valid": valid
                })
                
    results_df = pd.DataFrame(results)
    
    # Pareto tradeoffs
    max_recall = results_df["Recall"].max()
    max_recall_df = results_df[results_df["Recall"] == max_recall].iloc[0]
    
    max_prec = results_df["Precision"].max()
    max_prec_df = results_df[results_df["Precision"] == max_prec].iloc[0]
    
    max_f1 = results_df["F1"].max()
    max_f1_df = results_df[results_df["F1"] == max_f1].iloc[0]
    
    min_cost = results_df["ExpectedCost"].min()
    min_cost_df = results_df[results_df["ExpectedCost"] == min_cost].iloc[0]
    
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = f"artifacts/step14_policy_feasibility_review_{timestamp}_v1"
    os.makedirs(out_dir, exist_ok=True)
    
    results_df.to_csv(os.path.join(out_dir, "threshold_feasibility.csv"), index=False)
    
    report = {
        "validation_observations": len(df),
        "unique_probabilities_count": len(df["calibrated_p_safe_to_contest"].unique()),
        "unique_probabilities_distribution": obs_counts,
        "blueprint_explicit_minimum_precision_recall_floors": None, # Proven absent via grep
        "policy_0_80_origin": "User-resolved engineering decision",
        "tradeoffs": {
            "highest_recall": {
                "recall": float(max_recall),
                "precision": float(max_recall_df["Precision"])
            },
            "highest_precision": {
                "precision": float(max_prec),
                "recall": float(max_prec_df["Recall"])
            },
            "highest_f1": {
                "f1": float(max_f1),
                "precision": float(max_f1_df["Precision"]),
                "recall": float(max_f1_df["Recall"])
            },
            "lowest_expected_cost": {
                "cost": float(min_cost),
                "precision": float(min_cost_df["Precision"]),
                "recall": float(min_cost_df["Recall"])
            }
        },
        "feasible": bool(results_df["Overall_valid"].any())
    }
    
    with open(os.path.join(out_dir, "feasibility_review.json"), "w") as f:
        json.dump(report, f, indent=2)
        
    print(f"Feasibility review artifact written to {out_dir}")

if __name__ == "__main__":
    main()
