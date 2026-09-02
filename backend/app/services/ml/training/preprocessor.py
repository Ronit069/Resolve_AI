from typing import List, Dict, Any, Tuple
import pandas as pd
import numpy as np

# We'll use pandas DataFrame for features
class Preprocessor:
    def __init__(self):
        self.categorical_features = ['reason_code', 'payment_method']
        self.boolean_features = [
            'amount_match', 'order_id_match', 'tracking_match', 'timeline_valid',
            'refund_exists', 'shipment_available'
        ]
        self.numerical_features = [
            'required_evidence_coverage', 'missing_required_count', 'evidence_count',
            'customer_match_score', 'days_delivery_to_dispute', 'contradiction_count',
            'high_severity_contradictions', 'avg_ocr_confidence', 'min_ocr_confidence',
            'document_quality_score', 'dispute_amount', 'disputed_amount_ratio',
            'days_to_deadline'
        ]
        self.is_fitted = False
        
    def fit(self, examples: List[Dict[str, Any]]):
        """
        Fits preprocessing state. For CatBoost, there isn't much to fit (no OneHotEncoder needed),
        but we establish the state here in case we need to track categorical vocabularies later.
        """
        # Just record that we are fitted.
        self.is_fitted = True
        return self

    def transform(self, examples: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Transforms ML examples into X (features DataFrame) and y (target Series).
        """
        if not self.is_fitted:
            raise RuntimeError("Preprocessor must be fitted before transform")

        rows = []
        labels = []
        
        for ex in examples:
            features = ex["features"]
            row = {}
            
            # Categorical: Impute None to "UNKNOWN"
            for cat_feat in self.categorical_features:
                val = features.get(cat_feat)
                row[cat_feat] = str(val) if val is not None else "UNKNOWN"
                
            # Boolean features (we convert True/False to 1.0/0.0, and None to np.nan)
            for bool_feat in self.boolean_features:
                val = features.get(bool_feat)
                if val is None:
                    row[bool_feat] = np.nan
                else:
                    row[bool_feat] = 1.0 if val else 0.0
                    
            # Numerical features: Keep native. None to np.nan.
            for num_feat in self.numerical_features:
                val = features.get(num_feat)
                if val is None:
                    row[num_feat] = np.nan
                else:
                    row[num_feat] = float(val)
                    
            rows.append(row)
            labels.append(ex["label"])
            
        X = pd.DataFrame(rows)
        y = pd.Series(labels)
        
        # Ensure ordered columns
        ordered_cols = self.categorical_features + self.boolean_features + self.numerical_features
        X = X[ordered_cols]
        
        return X, y
