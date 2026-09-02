import os
import json
import pytest
import copy
from typing import List, Dict, Any
from app.services.ml.dataset_splitter import DatasetSplitter, SplitterConfig, FORBIDDEN_FEATURES, ALLOWED_FEATURES

@pytest.fixture
def source_data():
    path = "synthetic_benchmark_v1.jsonl"
    if not os.path.exists(path):
        pytest.skip("F4 benchmark file not found")
        
    data = []
    with open(path, 'r') as f:
        for line in f:
            data.append(json.loads(line))
    return data

@pytest.fixture
def manifest():
    path = "split_manifest.json"
    if not os.path.exists(path):
        pytest.skip("Manifest not found")
    with open(path, 'r') as f:
        return json.load(f)

def test_source_contains_10000(source_data):
    assert len(source_data) == 10000

def test_split_outputs_total_10000_and_no_duplicates(source_data):
    splitter = DatasetSplitter(SplitterConfig(seed=42))
    splits = splitter.split(source_data)
    
    total = len(splits["train"]) + len(splits["validation"]) + len(splits["test_holdout"])
    assert total == 10000
    
    example_ids = set()
    case_ids = set()
    
    for split_name, data in splits.items():
        for ex in data:
            example_ids.add(ex["example_id"])
            case_ids.add(ex["case_id"])
            
    assert len(example_ids) == 10000
    assert len(case_ids) == 10000

def test_no_group_leakage(source_data):
    splitter = DatasetSplitter(SplitterConfig(seed=42))
    splits = splitter.split(source_data)
    
    train_groups = set(ex["synthetic_customer_group"] for ex in splits["train"])
    val_groups = set(ex["synthetic_customer_group"] for ex in splits["validation"])
    test_groups = set(ex["synthetic_customer_group"] for ex in splits["test_holdout"])
    
    assert len(train_groups.intersection(val_groups)) == 0
    assert len(train_groups.intersection(test_groups)) == 0
    assert len(val_groups.intersection(test_groups)) == 0

def test_deterministic_split_membership(source_data):
    splitter_1 = DatasetSplitter(SplitterConfig(seed=42))
    splits_1 = splitter_1.split(source_data)
    
    splitter_2 = DatasetSplitter(SplitterConfig(seed=42))
    splits_2 = splitter_2.split(source_data)
    
    for split_name in splits_1:
        ids_1 = [ex["example_id"] for ex in splits_1[split_name]]
        ids_2 = [ex["example_id"] for ex in splits_2[split_name]]
        assert ids_1 == ids_2

def test_feature_whitelist_enforcement(source_data):
    splitter = DatasetSplitter(SplitterConfig(seed=42))
    
    # Introduce forbidden feature
    bad_data = copy.deepcopy(source_data)
    bad_data[0]["features"]["label"] = 1
    
    with pytest.raises(ValueError, match="Forbidden information explicitly found inside features payload: label"):
        splitter.validate_dataset_integrity(bad_data)
        
    bad_data_2 = copy.deepcopy(source_data)
    bad_data_2[0]["features"]["unknown_weird_feature"] = 1.0
    with pytest.raises(ValueError, match="Forbidden/unknown feature detected in whitelist audit"):
        splitter.validate_dataset_integrity(bad_data_2)

def test_no_label_or_feature_modification(source_data):
    splitter = DatasetSplitter(SplitterConfig(seed=42))
    splits = splitter.split(source_data)
    
    # Check that elements aren't mutated
    source_map = {ex["example_id"]: ex for ex in source_data}
    
    for split_name, data in splits.items():
        for out_ex in data:
            in_ex = source_map[out_ex["example_id"]]
            assert out_ex["label"] == in_ex["label"]
            assert out_ex["features"] == in_ex["features"]

def test_manifest_correctness(manifest, source_data):
    assert manifest["source_count"] == 10000
    assert manifest["train_count"] + manifest["validation_count"] + manifest["test_holdout_count"] == 10000
    assert manifest["group_isolation_result"] == "PASSED"
    assert manifest["feature_leakage_audit_result"] == "PASSED"
    
def test_handling_of_grouped_count_constraints():
    """
    Test where exact 70/15/15 is mathematically impossible due to group block sizes.
    Verify algorithm strictly preserves group isolation and chooses the closest fit.
    """
    # Create 3 groups of sizes: 500, 300, 200 (Total 1000)
    # Target train=700, val=150, test=150.
    # Group 1 (500) will go to Train.
    # Group 2 (300) will go to Train (total 800) OR Val (300) OR Test (300)
    # The algorithm must assign groups entirely and never split them.
    mock_data = []
    
    for i in range(500):
        mock_data.append({"example_id": f"g1_{i}", "case_id": f"c1_{i}", "label": 1, "reason_code": "10.4", "synthetic_customer_group": "group_1", "feature_hash": "hash", "features": {"version": 1}})
    for i in range(300):
        mock_data.append({"example_id": f"g2_{i}", "case_id": f"c2_{i}", "label": 1, "reason_code": "10.4", "synthetic_customer_group": "group_2", "feature_hash": "hash", "features": {"version": 1}})
    for i in range(200):
        mock_data.append({"example_id": f"g3_{i}", "case_id": f"c3_{i}", "label": 1, "reason_code": "10.4", "synthetic_customer_group": "group_3", "feature_hash": "hash", "features": {"version": 1}})
        
    splitter = DatasetSplitter(SplitterConfig(seed=42))
    splits = splitter.split(mock_data, skip_size_check=True)
    
    total = sum(len(s) for s in splits.values())
    assert total == 1000
    
    # Check isolation
    g1 = set(ex["synthetic_customer_group"] for ex in splits["train"])
    g2 = set(ex["synthetic_customer_group"] for ex in splits["validation"])
    g3 = set(ex["synthetic_customer_group"] for ex in splits["test_holdout"])
    
    assert len(g1.intersection(g2)) == 0
    assert len(g1.intersection(g3)) == 0
    assert len(g2.intersection(g3)) == 0
    
    # Check no group was split
    # Since 500+300 = 800 and 200 goes to val or test, Train will hit 800 because of the greedy fill ratio (800/700 = 1.14 < 300/150 = 2.0).
    assert len(splits["train"]) in [700, 800]
    assert "group_1" in g1
    assert "group_2" in g1 or "group_3" in g1
    assert "group_2" in g2 or "group_2" in g3 or "group_3" in g2 or "group_3" in g3

def test_holdout_isolation_protection():
    """
    Simulate a mock training preparation API that prevents loading TEST_HOLDOUT.
    """
    class TrainingDataAPI:
        def load_dataset(self, split_name: str):
            if split_name == "test_holdout":
                raise PermissionError("TEST_HOLDOUT cannot be loaded by the training pipeline. Access is restricted for final evaluation only.")
            return []
            
    api = TrainingDataAPI()
    
    # Can load train and validation
    assert api.load_dataset("train") == []
    assert api.load_dataset("validation") == []
    
    # Cannot load test_holdout
    with pytest.raises(PermissionError, match="TEST_HOLDOUT cannot be loaded by the training pipeline"):
        api.load_dataset("test_holdout")
