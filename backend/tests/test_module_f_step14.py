import os
import json
import hashlib
import pandas as pd
import pytest
from unittest.mock import patch

from optimize_thresholds_step14 import (
    hash_file,
    get_latest_dir,
    calculate_3way_cost,
    main
)

def test_test_holdout_firewall():
    with pytest.raises(PermissionError, match="TEST_HOLDOUT"):
        hash_file("data/test_holdout.jsonl")
        
def test_recall_and_cost_fn_semantics_are_distinct():
    """Ensure distinct FN semantics for standard recall vs expected cost."""
    df = pd.DataFrame({
        "calibrated_p_safe_to_contest": [0.1, 0.5, 0.9],
        "true_label": [0, 1, 1]
    })
    
    metrics = calculate_3way_cost(df, 0.2, 0.8, c_fp=50.0, c_fn=100.0, c_review=5.0)
    
    assert metrics["accept_count"] == 1
    assert metrics["review_count"] == 1
    assert metrics["contest_count"] == 1
    
    assert metrics["fp_count"] == 0
    assert metrics["tp_count"] == 1
    
    assert metrics["fn_count"] == 1  # Metric FN (includes REVIEW)
    assert metrics["recall"] == 0.5  # REVIEW observation remains in denominator!
    assert metrics["precision"] == 1.0
    assert metrics["expected_cost"] == 5.0  # Proves cost FN is 0, not 1!

@patch("optimize_thresholds_step14.pd.DataFrame.to_csv")
@patch("optimize_thresholds_step14.json.dump")
@patch("optimize_thresholds_step14.os.makedirs")
@patch("optimize_thresholds_step14.hash_file", return_value="dummy_hash")
@patch("optimize_thresholds_step14.open")
def test_final_policy_resolution(
    mock_open, mock_hash, mock_makedirs, mock_json_dump, mock_to_csv
):
    """
    Test that the final unconstrained minimum expected cost policy is selected.
    We test against the real F13 artifact to verify exact numbers.
    """
    main()
    
    args, kwargs = mock_json_dump.call_args
    policy = args[0]
    
    # 1 & 2. No artificial 0.80 floors
    assert policy["minimum_precision"] is None
    assert policy["minimum_recall"] is None
    assert policy["constraint_status"] == "NO_NUMERIC_PRECISION_RECALL_FLOORS"
    assert policy["primary_objective"] == "MINIMIZE_EXPECTED_COST"
    
    # 3 & 4. T_accept < T_contest and exhaustive grid
    assert policy["t_accept"] < policy["t_contest"]
    assert "exhaustive_unique_validation_probabilities_plus_0_and_1" in policy["threshold_grid_method"]
    
    # 7 & 8. Minimum cost selected with deterministic tie-break
    assert "F1 -> Precision -> Recall -> narrowest REVIEW band" in policy["tie_break_policy"]
    
    # 9, 10, 11, 12, 13. Exact expected numbers -- deterministic given
    # seed=42 throughout the offline pipeline. Updated for the issue #9/#10
    # training-data change (non-null extracted_fields/quality_assessments/
    # links, four reason codes instead of just 10.4): that changed what
    # the benchmark actually generates, so these values changed with it,
    # same as any other real training-data update would.
    assert policy["t_accept"] == pytest.approx(0.6470588235294118, abs=1e-5)
    assert policy["t_contest"] == 1.0
    assert policy["expected_cost"] == 2240.0
    assert policy["precision"] == 1.0
    assert policy["recall"] == pytest.approx(0.42684766214177977, abs=1e-2)
    assert policy["f1"] == pytest.approx(0.5983086680761099, abs=1e-2)
    
    # 14, 18. Firewalled and frozen
    assert policy["test_holdout_hash"] == "NOT COMPUTED"

def test_no_legacy_f7_imports():
    with open("optimize_thresholds_step14.py") as f:
        content = f.read()
    assert "calibrate_and_optimize" not in content
