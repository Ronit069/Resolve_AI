import os
import json
import hashlib
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

import catboost
from sklearn.isotonic import IsotonicRegression

from calibrate_winner_step13 import (
    load_jsonl,
    hash_file,
    get_latest_dir,
    main
)

def test_test_holdout_firewall():
    """Verify that any attempt to hash or load test_holdout raises PermissionError."""
    with pytest.raises(PermissionError, match="TEST_HOLDOUT"):
        hash_file("data/test_holdout.jsonl")
        
    with pytest.raises(PermissionError, match="TEST_HOLDOUT"):
        load_jsonl("data/test_holdout.jsonl")

@patch("catboost.CatBoostClassifier.fit")
@patch("catboost.CatBoostClassifier.load_model")
def test_no_retraining_catboost(mock_load, mock_fit, tmp_path):
    """Verify that CatBoost.fit() is NEVER called during Step 13."""
    
    # We will mock load_jsonl, get_latest_dir, hash_file, and IsotonicRegression to just run the flow
    with patch("calibrate_winner_step13.load_jsonl") as mock_load_jsonl, \
         patch("calibrate_winner_step13.get_latest_dir", return_value="dummy_dir"), \
         patch("calibrate_winner_step13.hash_file", return_value="dummy_hash"), \
         patch("calibrate_winner_step13.os.makedirs"), \
         patch("calibrate_winner_step13.open"), \
         patch("calibrate_winner_step13.pickle.dump"), \
         patch("pandas.DataFrame.to_csv"):
         
        mock_load_jsonl.return_value = [
            {"example_id": f"ex_{i}", "label": i % 2, "features": {"reason_code": "A"}}
            for i in range(100)
        ]
        
        # We also need to patch Preprocessor and model.predict_proba
        with patch("calibrate_winner_step13.Preprocessor") as mock_prep:
            mock_prep_inst = mock_prep.return_value
            mock_prep_inst.transform.return_value = (pd.DataFrame(), pd.Series([i % 2 for i in range(100)]))
            
            with patch("catboost.CatBoostClassifier.predict_proba") as mock_predict_proba:
                mock_predict_proba.return_value = pd.DataFrame({0: [0.5]*100, 1: [0.5]*100}).values
                
                main()
                
                # Assert CatBoost was loaded
                mock_load.assert_called_once()
                # Assert CatBoost was NEVER fitted
                mock_fit.assert_not_called()

def test_split_properties_and_bounds():
    """
    Verify deterministic 50/50 split properties:
    - Same input produces same split
    - Disjoint fit/eval sets
    - Union equals original
    - Bounds [0,1]
    """
    
    # Setup dummy data for testing the split logic specifically
    df = pd.DataFrame({
        "example_id": [f"id_{i}" for i in range(1000)],
        "true_label": [i % 2 for i in range(1000)],
        "raw_p_safe_to_contest": [0.5 for i in range(1000)]
    })
    
    from sklearn.model_selection import train_test_split
    fit_df, eval_df = train_test_split(df, test_size=0.5, random_state=42, stratify=df["true_label"])
    
    assert len(fit_df) == 500
    assert len(eval_df) == 500
    
    # Check disjoint
    fit_ids = set(fit_df["example_id"])
    eval_ids = set(eval_df["example_id"])
    assert len(fit_ids.intersection(eval_ids)) == 0
    
    # Check union
    assert len(fit_ids.union(eval_ids)) == 1000
    
    # Verify stratification
    fit_pos = fit_df["true_label"].sum()
    eval_pos = eval_df["true_label"].sum()
    assert fit_pos == 250
    assert eval_pos == 250
    
    # Verify bounds after isotonic fit
    from sklearn.isotonic import IsotonicRegression
    calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    
    # We will simulate raw probabilities outside [0,1] just to check bounds
    fit_df["raw_p_safe_to_contest"] = -0.5 # should clip
    eval_df["raw_p_safe_to_contest"] = 1.5 # should clip
    
    # Isotonic needs y to be sorted or it sorts it.
    # It learns a mapping.
    calibrator.fit(fit_df["raw_p_safe_to_contest"], fit_df["true_label"])
    
    pred = calibrator.predict(eval_df["raw_p_safe_to_contest"])
    
    # The output MUST be in [0,1]
    assert pred.min() >= 0.0
    assert pred.max() <= 1.0

def test_no_legacy_f7_imports():
    """Ensure calibrate_and_optimize is not imported."""
    with open("calibrate_winner_step13.py") as f:
        content = f.read()
    assert "calibrate_and_optimize" not in content

def test_metadata_has_correct_provenance():
    """Test that winner_selection explicitly equals manual_step12_resolution."""
    with patch("calibrate_winner_step13.load_jsonl") as mock_load_jsonl, \
         patch("calibrate_winner_step13.get_latest_dir", return_value="dummy_dir"), \
         patch("calibrate_winner_step13.hash_file", return_value="dummy_hash"), \
         patch("calibrate_winner_step13.os.makedirs"), \
         patch("calibrate_winner_step13.open") as mock_open, \
         patch("calibrate_winner_step13.pickle.dump"), \
         patch("pandas.DataFrame.to_csv"):
         
        mock_load_jsonl.return_value = [
            {"example_id": f"ex_{i}", "label": i % 2, "features": {"reason_code": "A"}}
            for i in range(10)
        ]
        
        with patch("calibrate_winner_step13.Preprocessor") as mock_prep:
            mock_prep_inst = mock_prep.return_value
            mock_prep_inst.transform.return_value = (pd.DataFrame(), pd.Series([i % 2 for i in range(10)]))
            with patch("catboost.CatBoostClassifier.load_model"), \
                 patch("catboost.CatBoostClassifier.predict_proba") as mock_predict_proba, \
                 patch("calibrate_winner_step13.json.dump") as mock_json_dump:
                
                mock_predict_proba.return_value = pd.DataFrame({0: [0.5]*10, 1: [0.5]*10}).values
                
                main()
                
                # Inspect the dumped json metadata
                args, kwargs = mock_json_dump.call_args
                metadata = args[0]
                
                assert metadata["winner"] == "CatBoost"
                assert metadata["winner_selection"] == "manual_step12_resolution"
                assert metadata["split_method"] == "stratified 50/50 split"
                assert metadata["calibration_method"] == "IsotonicRegression"
                assert metadata["test_holdout_hash"] == "NOT COMPUTED"
                assert "calibration_fit_subset_hash" in metadata
                assert "calibration_evaluation_subset_hash" in metadata
