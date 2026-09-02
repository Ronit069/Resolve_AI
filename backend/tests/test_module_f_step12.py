import os
import json
import pandas as pd
import pytest
from unittest import mock

from compare_models_step12 import hash_file, evaluate_ml_candidate_from_csv, get_ml_candidate_from_metadata, main

def test_test_holdout_firewall():
    with pytest.raises(PermissionError, match="TEST_HOLDOUT is strictly forbidden"):
        hash_file("synthetic_benchmark_v1_test_holdout.jsonl")
        
    with pytest.raises(PermissionError, match="TEST_HOLDOUT is strictly forbidden"):
        evaluate_ml_candidate_from_csv("synthetic_benchmark_v1_test_holdout.csv")

@mock.patch("compare_models_step12.calculate_expected_cost")
def test_evaluate_ml_candidate_thresholds(mock_cost, tmp_path):
    csv_path = tmp_path / "dummy.csv"
    df = pd.DataFrame({
        "example_id": ["e1", "e2", "e3"],
        "true_label": [1, 0, 1],
        "p_safe_to_contest": [0.9, 0.4, 0.5] # 0.5 should be evaluated as 1
    })
    df.to_csv(csv_path, index=False)
    
    mock_cost.return_value = 100.0
    
    res = evaluate_ml_candidate_from_csv(str(csv_path))
    
    assert len(res["example_ids"]) == 3
    mock_cost.assert_called_once()
    kwargs = mock_cost.call_args.kwargs
    assert kwargs["threshold"] == 0.5

@mock.patch("compare_models_step12.get_ml_candidate_from_metadata")
@mock.patch("compare_models_step12.evaluate_ml_candidate_from_csv")
@mock.patch("compare_models_step12.get_latest_dir")
@mock.patch("builtins.open", new_callable=mock.mock_open)
def test_near_floating_point_tie(mock_open, mock_get_dir, mock_eval_csv, mock_eval_meta):
    mock_get_dir.return_value = "dummy_dir"
    
    mock_open.return_value.read.return_value = json.dumps({
        "metrics": {
            "classification": {"precision": 0.8, "recall": 0.8, "f1_score": 0.8},
            "business_cost": {"expected_cost": 5000},
            "confusion_matrix": {}
        },
        "provenance": {"dataset_hash": "dummy_hash"}
    })
    
    # 0.8827385205094183 vs 0.8827385205094184
    mock_eval_csv.side_effect = [
        {"example_ids": ["e1"], "dataset_hash": "dummy_hash", "metrics": {"PR-AUC": 0.8827385205094184, "Status": "ML Candidate"}}
    ] * 2
    mock_eval_meta.return_value = {
        "example_ids": ["e1"], "dataset_hash": "dummy_hash", "metrics": {"PR-AUC": 0.8827385205094183, "Status": "ML Candidate"}
    }
    
    with pytest.raises(RuntimeError, match="Blueprint Ambiguity: PR-AUC is mathematically tied"):
        main()

@mock.patch("compare_models_step12.hash_file")
@mock.patch("compare_models_step12.get_ml_candidate_from_metadata")
@mock.patch("compare_models_step12.evaluate_ml_candidate_from_csv")
@mock.patch("compare_models_step12.get_latest_dir")
@mock.patch("builtins.open", new_callable=mock.mock_open)
@mock.patch("os.makedirs")
@mock.patch("json.dump")
def test_genuine_pr_auc_difference(mock_dump, mock_makedirs, mock_open, mock_get_dir, mock_eval_csv, mock_eval_meta, mock_hash):
    mock_hash.return_value = "dummy_hash"
    mock_get_dir.return_value = "dummy_dir"
    
    mock_open.return_value.read.return_value = json.dumps({
        "metrics": {
            "classification": {"precision": 0.8, "recall": 0.8, "f1_score": 0.8},
            "business_cost": {"expected_cost": 5000},
            "confusion_matrix": {}
        },
        "provenance": {"dataset_hash": "dummy_hash"}
    })
    
    # 0.88273 vs 0.88274
    mock_eval_csv.side_effect = [
        {"example_ids": ["e1"], "dataset_hash": "dummy_hash", "metrics": {"PR-AUC": 0.88273, "Status": "ML Candidate"}}, # F9
        {"example_ids": ["e1"], "dataset_hash": "dummy_hash", "metrics": {"PR-AUC": 0.88274, "Status": "ML Candidate"}}, # F11
    ]
    mock_eval_meta.return_value = {
        "example_ids": ["e1"], "dataset_hash": "dummy_hash", "metrics": {"PR-AUC": 0.88272, "Status": "ML Candidate"} # F6
    }
    
    # This should not raise an error, LightGBM (F11) should win
    main()
    
    # Check that it selected LightGBM
    saved_report = mock_dump.call_args[0][0]
    assert saved_report["winner_selection"]["selected_model"] == "LightGBM"

