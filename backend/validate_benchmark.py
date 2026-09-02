import json
import os

path = "synthetic_benchmark_v1.jsonl"
example_ids = set()
case_ids = set()
valid_count = 0
malformed_count = 0
labels = {0: 0, 1: 0}
families = {}

with open(path, 'r') as f:
    for line in f:
        try:
            row = json.loads(line)
            valid_count += 1
            example_ids.add(row["example_id"])
            case_ids.add(row["case_id"])
            
            # Assertions
            assert "features" in row
            assert "label" in row
            assert row["label"] in [0, 1]
            assert "feature_hash" in row
            
            labels[row["label"]] += 1
            sf = row.get("scenario_family", "UNKNOWN")
            families[sf] = families.get(sf, 0) + 1
        except Exception as e:
            malformed_count += 1
            print(f"Error parsing row: {e}")

print(f"Total valid JSON lines: {valid_count}")
print(f"Total malformed lines: {malformed_count}")
print(f"Unique example_ids: {len(example_ids)}")
print(f"Unique case_ids: {len(case_ids)}")
print(f"Label distribution: {labels}")
print(f"Scenario distribution: {families}")

if valid_count == 10000 and len(example_ids) == 10000 and len(case_ids) == 10000 and malformed_count == 0:
    print("OUTPUT VALIDATION PASSED")
else:
    print("OUTPUT VALIDATION FAILED")
