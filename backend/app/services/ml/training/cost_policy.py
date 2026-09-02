import pandas as pd
import numpy as np

def calculate_expected_cost(
    y_true: pd.Series, 
    y_pred_prob: pd.Series, 
    threshold: float = 0.5,
    c_fp: float = 50.0,
    c_fn: float = 100.0,
    c_review: float = 5.0
) -> float:
    """
    Calculates the expected cost according to cost_policy_v1.
    ExpectedCost = (C_FP × FP) + (C_FN × FN) + (C_REVIEW × N_review)
    """
    y_pred = (y_pred_prob >= threshold).astype(int)
    
    fp = np.sum((y_pred == 1) & (y_true == 0))
    fn = np.sum((y_pred == 0) & (y_true == 1))
    
    # In ResolveAI, N_review means anything we do not automate.
    # Prediction of 1 means SAFE_TO_CONTEST (Automated).
    # Prediction of 0 means NOT_SAFE (Sent to human review).
    n_review = np.sum(y_pred == 0)
    
    cost = (c_fp * fp) + (c_fn * fn) + (c_review * n_review)
    return float(cost)
