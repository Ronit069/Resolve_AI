import random
import hashlib
import json
from collections import defaultdict
from typing import Dict, Any, List, Tuple
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

# F2 Whitelist
ALLOWED_FEATURES = {
    'required_evidence_coverage', 'missing_required_count', 'evidence_count', 
    'amount_match', 'order_id_match', 'tracking_match', 'customer_match_score', 
    'timeline_valid', 'days_delivery_to_dispute', 'contradiction_count', 
    'high_severity_contradictions', 'avg_ocr_confidence', 'min_ocr_confidence', 
    'document_quality_score', 'reason_code', 'payment_method', 'dispute_amount', 
    'disputed_amount_ratio', 'refund_exists', 'shipment_available', 'days_to_deadline', 
    'version'
}

FORBIDDEN_FEATURES = {
    'label', 'label_rationale', 'blocking_reasons', 'hard_block_indicators',
    'scenario_family', 'synthetic_customer_group', 'example_id', 'case_id'
}

class SplitterConfig(BaseModel):
    train_ratio: float = 0.70
    validation_ratio: float = 0.15
    test_ratio: float = 0.15
    seed: int = 42

class DatasetSplitter:
    def __init__(self, config: SplitterConfig = SplitterConfig()):
        self.config = config
        self.rng = random.Random(config.seed)

    def validate_dataset_integrity(self, examples: List[Dict[str, Any]], skip_size_check: bool = False):
        if not skip_size_check and len(examples) != 10000:
            raise ValueError(f"Expected exactly 10,000 examples, got {len(examples)}")
            
        example_ids = set()
        case_ids = set()
        
        for ex in examples:
            if not isinstance(ex.get("features"), dict):
                raise ValueError("features must be a dictionary")
                
            if ex.get("label") not in [0, 1]:
                raise ValueError(f"Label must be 0 or 1, got {ex.get('label')}")
                
            for required_field in ["example_id", "case_id", "feature_hash", "synthetic_customer_group", "reason_code"]:
                if required_field not in ex:
                    raise ValueError(f"Missing required field {required_field}")
                    
            example_ids.add(ex["example_id"])
            case_ids.add(ex["case_id"])
            
            # Leakage Audit
            features = ex["features"]
            
            # Explicit forbidden check first
            for forbidden in FORBIDDEN_FEATURES:
                if forbidden in features:
                    raise ValueError(f"Forbidden information explicitly found inside features payload: {forbidden}")
                    
            # Whitelist check
            for key in features.keys():
                if key not in ALLOWED_FEATURES:
                    raise ValueError(f"Forbidden/unknown feature detected in whitelist audit: {key}")
                    
        if not skip_size_check:
            if len(example_ids) != 10000:
                raise ValueError(f"Duplicate example_ids found. Expected 10000, got {len(example_ids)}")
            if len(case_ids) != 10000:
                raise ValueError(f"Duplicate case_ids found. Expected 10000, got {len(case_ids)}")
            
    def _group_by_customer(self, examples: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        groups = defaultdict(list)
        for ex in examples:
            groups[ex["synthetic_customer_group"]].append(ex)
        return dict(groups)

    def _calculate_stratum_dist(self, group_rows: List[Dict[str, Any]]) -> Dict[Tuple[int, str], int]:
        dist = defaultdict(int)
        for row in group_rows:
            dist[(row["label"], row["reason_code"])] += 1
        return dist

    def split(self, examples: List[Dict[str, Any]], skip_size_check: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        self.validate_dataset_integrity(examples, skip_size_check=skip_size_check)
        
        groups = self._group_by_customer(examples)
        
        # Sort keys first to guarantee identical traversal order before shuffling, making it fully deterministic regardless of input grouping order
        group_keys = list(groups.keys())
        group_keys.sort()
        self.rng.shuffle(group_keys)
        
        total_examples = len(examples)
        
        # Determine global joint distribution targets
        global_dist = self._calculate_stratum_dist(examples)
        
        splits = {
            "train": {"data": [], "target": self.config.train_ratio, "dist": defaultdict(int), "count": 0},
            "validation": {"data": [], "target": self.config.validation_ratio, "dist": defaultdict(int), "count": 0},
            "test_holdout": {"data": [], "target": self.config.test_ratio, "dist": defaultdict(int), "count": 0},
        }
        
        for k in group_keys:
            group_rows = groups[k]
            g_size = len(group_rows)
            g_dist = self._calculate_stratum_dist(group_rows)
            
            best_split = None
            best_score = float('inf')
            
            for split_name, split_info in splits.items():
                # 1. Deficit penalty: which split is lagging behind its proportional fill target?
                # A split's target count is target_ratio * total_examples
                target_count = split_info["target"] * total_examples
                # We want to fill splits proportionally. The split with the lowest (current_count / target_count) is the most starved.
                # Adding this group gives it new_count.
                new_count = split_info["count"] + g_size
                fill_ratio = new_count / target_count if target_count > 0 else float('inf')
                
                # 2. Stratification penalty (how far off the global stratum distribution are we?)
                # We calculate this over (label, reason_code)
                strat_penalty = 0.0
                for stratum_key, global_count in global_dist.items():
                    global_ratio = global_count / total_examples
                    
                    new_stratum_count = split_info["dist"][stratum_key] + g_dist.get(stratum_key, 0)
                    new_stratum_ratio = new_stratum_count / new_count if new_count > 0 else 0
                    
                    strat_penalty += abs(new_stratum_ratio - global_ratio)
                    
                # Combine penalties
                # fill_ratio goes from ~0.0 to 1.0. We want to pick the split with the LOWEST fill_ratio.
                # strat_penalty goes from ~0.0 to ~1.0. 
                # We strongly prioritize keeping sizes proportional (fill_ratio), using stratification as a secondary decider
                total_penalty = fill_ratio * 10 + strat_penalty
                
                if total_penalty < best_score:
                    best_score = total_penalty
                    best_split = split_name
                    
            # Assign to best split
            splits[best_split]["data"].extend(group_rows)
            splits[best_split]["count"] += g_size
            for sk, sc in g_dist.items():
                splits[best_split]["dist"][sk] += sc
                
        # Sort each split deterministically by case_id
        for split_name in splits:
            splits[split_name]["data"].sort(key=lambda x: x["case_id"])
            
        return {
            "train": splits["train"]["data"],
            "validation": splits["validation"]["data"],
            "test_holdout": splits["test_holdout"]["data"]
        }
