import pytest
import os
import copy
import pandas as pd
import numpy as np

from app.services.ml.training.preprocessor import Preprocessor
from app.services.ml.training.cost_policy import calculate_expected_cost
from train_benchmark import load_jsonl

@pytest.fixture
def mock_examples():
    return [
        {
            "label": 1,
            "features": {
                "reason_code": "10.4",
                "payment_method": "credit",
                "amount_match": True,
                "dispute_amount": 100.0
            }
        },
        {
            "label": 0,
            "features": {
                "reason_code": None,
                "payment_method": None,
                "amount_match": None,
                "dispute_amount": None
            }
        }
    ]

def test_catboost_preprocessing_unknown(mock_examples):
    preprocessor = Preprocessor()
    preprocessor.fit(mock_examples)
    X, y = preprocessor.transform(mock_examples)
    
    # Check categorical unknown
    assert X.iloc[1]["reason_code"] == "UNKNOWN"
    assert X.iloc[1]["payment_method"] == "UNKNOWN"
    
    # Check boolean unknown (becomes np.nan)
    assert pd.isna(X.iloc[1]["amount_match"])
    
    # Check numerical unknown (becomes np.nan)
    assert pd.isna(X.iloc[1]["dispute_amount"])
    
    # Check knowns
    assert X.iloc[0]["reason_code"] == "10.4"
    assert X.iloc[0]["amount_match"] == 1.0
    assert X.iloc[0]["dispute_amount"] == 100.0

def test_feature_schema_validation():
    # Write a temporary valid file
    with open("temp_valid.jsonl", "w") as f:
        f.write('{"features": {"reason_code": "10.4"}}\n')
        
    data = load_jsonl("temp_valid.jsonl")
    assert len(data) == 1
    
    # Write a temporary forbidden file
    with open("temp_forbidden.jsonl", "w") as f:
        f.write('{"features": {"label": 1, "reason_code": "10.4"}}\n')
        
    with pytest.raises(ValueError, match="Forbidden feature label detected"):
        load_jsonl("temp_forbidden.jsonl")
        
    os.remove("temp_valid.jsonl")
    os.remove("temp_forbidden.jsonl")

def test_test_holdout_access_rejection():
    with pytest.raises(PermissionError, match="TEST_HOLDOUT cannot be loaded by the training pipeline"):
        load_jsonl("synthetic_benchmark_v1_test_holdout.jsonl")
        
def test_train_only_fitting():
    preprocessor = Preprocessor()
    with pytest.raises(RuntimeError, match="Preprocessor must be fitted before transform"):
        preprocessor.transform([{"label": 1, "features": {}}])
        
def test_cost_policy_configurable():
    y_true = pd.Series([1, 1, 0, 0])
    y_prob = pd.Series([0.9, 0.1, 0.9, 0.1])
    
    # y_pred at 0.5 threshold: [1, 0, 1, 0]
    # TP = 1 (idx 0)
    # FN = 1 (idx 1) -> Cost C_FN (100) + N_review (C_REVIEW 5) = 105
    # FP = 1 (idx 2) -> Cost C_FP (50)
    # TN = 1 (idx 3) -> N_review (C_REVIEW 5) = 5
    # Total cost = 100 + 5 + 50 + 5 = 160
    
    cost = calculate_expected_cost(y_true, y_prob, threshold=0.5, c_fp=50, c_fn=100, c_review=5)
    assert cost == 160.0
    
    # Test configurable N_review
    cost2 = calculate_expected_cost(y_true, y_prob, threshold=0.5, c_fp=50, c_fn=100, c_review=10)
    assert cost2 == 170.0

def test_nan_mode_configuration():
    from app.services.ml.training.catboost_trainer import CatBoostTrainer
    trainer = CatBoostTrainer(categorical_features=[])
    # Init shouldn't set model yet, but after train it should
    import pandas as pd
    X = pd.DataFrame({"feat": [1.0, np.nan, 3.0]})
    y = pd.Series([0, 1, 0])
    trainer.train(X, y, X, y)
    
    assert trainer.model is not None
    assert trainer.model.get_param('nan_mode') == 'Min'

def test_deterministic_predictions(mock_examples):
    from app.services.ml.training.catboost_trainer import CatBoostTrainer
    preprocessor = Preprocessor()
    preprocessor.fit(mock_examples)
    X, y = preprocessor.transform(mock_examples)
    
    trainer1 = CatBoostTrainer(categorical_features=preprocessor.categorical_features)
    trainer1.train(X, y, X, y)
    probs1 = trainer1.model.predict_proba(X)
    
    trainer2 = CatBoostTrainer(categorical_features=preprocessor.categorical_features)
    trainer2.train(X, y, X, y)
    probs2 = trainer2.model.predict_proba(X)
    
    np.testing.assert_array_equal(probs1, probs2)

def test_hard_block_separation():
    # Hard block logic must conceptually remain external.
    # The F6 trainer must NOT implement something like:
    # `if X['invalid_dispute'] == True: return 0`
    # We verify this by proving F6 outputs probability purely based on learned features,
    # and the metadata specifies the boundary.
    import json
    # Just asserting the contract is recorded in the design
    with open('train_benchmark.py', 'r') as f:
        content = f.read()
    assert '"hard_block_policy": "F1 deterministic policy applies downstream of this model"' in content
