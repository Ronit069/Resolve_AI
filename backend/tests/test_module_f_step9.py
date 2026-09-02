import os
import json
import numpy as np
import pandas as pd
import pytest
from unittest import mock

from app.services.ml.feature_builder import MLFeaturesV1
from train_logistic_baseline import extract_features, load_data, build_pipeline

def test_test_holdout_firewall():
    with pytest.raises(PermissionError, match="TEST_HOLDOUT is strictly forbidden"):
        load_data("synthetic_benchmark_v1_test_holdout.jsonl")
        
def test_extract_features_allowlist():
    # Only ml_features_v1 fields should be present
    dummy_data = [{
        "case_id": "c1",
        "example_id": "e1",
        "label": 1,
        "features": {
            "reason_code": "10.4",
            "payment_method": "card",
            "amount_match": True,
            "dispute_amount": 100,
            "forbidden_feature": "should_not_exist"
        }
    }]
    
    df, labels, example_ids = extract_features(dummy_data)
    
    assert "forbidden_feature" not in df.columns
    assert "case_id" not in df.columns
    assert "example_id" not in df.columns
    assert "label" not in df.columns
    assert "reason_code" in df.columns
    assert "dispute_amount" in df.columns
    
    # Verify count exactly matches the V1 schema count
    # Categorical (2) + Boolean (6) + Numerical (13) = 21 features
    assert len(df.columns) == 21
    
    # Assert values
    assert labels[0] == 1
    assert example_ids[0] == "e1"
        
def test_preprocessing_pipeline_missing_unknown():
    df = pd.DataFrame([
        {
            "reason_code": np.nan, # Categorical missing
            "amount_match": np.nan, # Boolean missing
            "dispute_amount": np.nan # Numerical missing
        },
        {
            "reason_code": "10.4",
            "amount_match": 1.0,
            "dispute_amount": 100.0
        }
    ])
    
    preprocessor = build_pipeline(
        numeric_cols=["dispute_amount"],
        cat_cols=["reason_code"],
        bool_cols=["amount_match"]
    )
    
    X_trans = preprocessor.fit_transform(df)
    
    # Categorical Reason Code: One-hot encoded. 'UNKNOWN' should be a category.
    # We expect reason_code_UNKNOWN and reason_code_10.4
    features = preprocessor.get_feature_names_out()
    assert any("UNKNOWN" in f for f in features)
    
    # Numerical missing should be median (100.0)
    # Boolean missing should be -1.0
    
    # So both rows for dispute_amount should be 100.0, scaled to 0.0
    # Let's verify numerical column (last column based on our transformer order: cat, bool, num)
    num_idx = list(features).index("num__dispute_amount")
    assert np.allclose(X_trans[:, num_idx], [0.0, 0.0]) # Scaled median
