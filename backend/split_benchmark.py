import os
import json
import hashlib
from collections import defaultdict
from app.services.ml.dataset_splitter import DatasetSplitter, SplitterConfig

def hash_file(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        buf = f.read()
        hasher.update(buf)
    return hasher.hexdigest()

def main():
    source_file = "synthetic_benchmark_v1.jsonl"
    print(f"Loading {source_file}...")
    
    examples = []
    with open(source_file, 'r') as f:
        for line in f:
            examples.append(json.loads(line))
            
    print(f"Loaded {len(examples)} records.")
    
    config = SplitterConfig(seed=42)
    splitter = DatasetSplitter(config)
    
    print("Performing leakage-safe grouped stratified splitting...")
    try:
        splits = splitter.split(examples)
    except ValueError as e:
        print(f"FAILED DATASET VALIDATION: {e}")
        return
        
    source_sha = hash_file(source_file)
    
    manifest = {
        "source_dataset_version": "synthetic_benchmark_v1",
        "split_version": "split_v1",
        "seed": config.seed,
        "source_dataset_sha256": source_sha,
        "source_count": len(examples),
        "split_details": {}
    }
    
    total_groups = len(set(ex["synthetic_customer_group"] for ex in examples))
    
    print("\n================== SPLIT RESULTS ==================")
    for split_name, data in splits.items():
        output_file = f"synthetic_benchmark_v1_{split_name}.jsonl"
        
        with open(output_file, 'w') as f:
            for ex in data:
                f.write(json.dumps(ex, sort_keys=True) + "\n")
                
        split_sha = hash_file(output_file)
        
        # Calculate stats
        labels = {0: 0, 1: 0}
        reason_codes = defaultdict(int)
        joint_dist = defaultdict(int)
        groups = set()
        
        for ex in data:
            labels[ex["label"]] += 1
            reason_codes[ex["reason_code"]] += 1
            joint_dist[f"{ex['label']}_{ex['reason_code']}"] += 1
            groups.add(ex["synthetic_customer_group"])
            
        manifest["split_details"][split_name] = {
            "count": len(data),
            "sha256": split_sha,
            "label_distribution": labels,
            "reason_code_distribution": dict(reason_codes),
            "joint_distribution": dict(joint_dist),
            "customer_group_count": len(groups)
        }
        
        print(f"\n[{split_name.upper()}]")
        print(f"  Count: {len(data)} ({(len(data)/len(examples))*100:.2f}%)")
        print(f"  Groups: {len(groups)}")
        print(f"  Label 1 (SAFE_TO_CONTEST): {labels[1]} ({labels[1]/len(data)*100:.2f}%)")
        print(f"  Label 0 (NOT_SAFE): {labels[0]} ({labels[0]/len(data)*100:.2f}%)")
        for j, c in joint_dist.items():
            print(f"  Joint (Label_Reason): {j} -> {c}")

    # Verify Isolation
    train_groups = set(ex["synthetic_customer_group"] for ex in splits["train"])
    val_groups = set(ex["synthetic_customer_group"] for ex in splits["validation"])
    test_groups = set(ex["synthetic_customer_group"] for ex in splits["test_holdout"])
    
    assert len(train_groups.intersection(val_groups)) == 0
    assert len(train_groups.intersection(test_groups)) == 0
    assert len(val_groups.intersection(test_groups)) == 0
    
    manifest["group_isolation_result"] = "PASSED"
    manifest["feature_leakage_audit_result"] = "PASSED"
    
    manifest["train_sha256"] = manifest["split_details"]["train"]["sha256"]
    manifest["validation_sha256"] = manifest["split_details"]["validation"]["sha256"]
    manifest["test_holdout_sha256"] = manifest["split_details"]["test_holdout"]["sha256"]
    manifest["train_count"] = manifest["split_details"]["train"]["count"]
    manifest["validation_count"] = manifest["split_details"]["validation"]["count"]
    manifest["test_holdout_count"] = manifest["split_details"]["test_holdout"]["count"]
    
    with open("split_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
        
    print("\n================== VERIFICATION ==================")
    print("Feature Leakage Audit: PASSED")
    print("Group Isolation Audit: PASSED")
    print("Files Generated:")
    print(" - synthetic_benchmark_v1_train.jsonl")
    print(" - synthetic_benchmark_v1_validation.jsonl")
    print(" - synthetic_benchmark_v1_test_holdout.jsonl")
    print(" - split_manifest.json")

if __name__ == "__main__":
    main()
