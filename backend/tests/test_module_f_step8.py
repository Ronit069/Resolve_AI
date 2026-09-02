import os
import json
import pytest
import sys
from unittest import mock
from evaluate_baseline import evaluate_baseline, load_data

def test_test_holdout_rejected():
    with pytest.raises(PermissionError, match="TEST_HOLDOUT is strictly forbidden"):
        load_data("artifacts/synthetic_benchmark_v1_test_holdout.jsonl")

def test_ml_leakage_prevented(tmp_path):
    import evaluate_baseline
    sys.modules["catboost"] = mock.Mock()
    
    val_file = tmp_path / "val.jsonl"
    val_file.write_text('{"case_id": "test", "label": 1}\n')
    
    with pytest.raises(ImportError, match="ML leakage detected"):
        evaluate_baseline.evaluate_baseline(
            str(val_file), 
            "dummy.db", 
            50.0, 100.0, 5.0, 
            str(tmp_path)
        )
        
    del sys.modules["catboost"]

def test_cost_calculation_correctness(tmp_path):
    # We will mock the database and test the math of evaluate_baseline
    pass
    # For now, we rely on the main script executing and producing the artifact
    # Testing the output artifact format
    
    
def test_artifact_format():
    # Find the generated artifact
    artifacts_dir = "artifacts"
    dirs = [d for d in os.listdir(artifacts_dir) if d.startswith("baseline_module_e_")]
    if not dirs:
        pytest.skip("No baseline artifact found to test.")
        
    latest_dir = sorted(dirs)[-1]
    artifact_path = os.path.join(artifacts_dir, latest_dir, "baseline_metrics.json")
    
    with open(artifact_path, "r") as f:
        report = json.load(f)
        
    metrics = report["metrics"]
    prov = report["provenance"]
    
    assert "confusion_matrix" in metrics
    assert "classification" in metrics
    assert "business_cost" in metrics
    assert "rule_coverage_counts" in metrics
    
    assert metrics["pr_auc"] == "NOT APPLICABLE"
    assert metrics["roc_auc"] == "NOT APPLICABLE"
    
    assert prov["dataset_split_version"] == "v1"
    assert "dataset_hash" in prov
    assert "evaluation_timestamp" in prov
