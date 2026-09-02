import numpy as np
import pandas as pd

from app.services.ml.feature_builder import MLFeaturesV1

class LightGBMPreprocessor:
    """
    Dedicated Preprocessor for LightGBM.
    Fits categorical vocabularies exclusively on TRAIN and casts string categories
    to pandas.CategoricalDtype to natively support LightGBM's categorical splits.
    Unseen categories in VALIDATION are safely mapped to NaN.
    """
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
        
        # Will map feature_name -> pd.CategoricalDtype
        self.categorical_dtypes = {}
        
    def fit(self, examples: list):
        """
        Fits categorical vocabularies strictly from the provided examples (TRAIN).
        """
        # Collect unique values for each categorical feature
        unique_categories = {feat: set() for feat in self.categorical_features}
        
        for ex in examples:
            features = ex["features"]
            for cat_feat in self.categorical_features:
                val = features.get(cat_feat)
                # Map missing to UNKNOWN exactly as CatBoost did for fairness
                str_val = str(val) if val is not None else "UNKNOWN"
                unique_categories[cat_feat].add(str_val)
                
        # Freeze into pandas categorical dtypes (sorted for determinism)
        for cat_feat in self.categorical_features:
            sorted_cats = sorted(list(unique_categories[cat_feat]))
            self.categorical_dtypes[cat_feat] = pd.CategoricalDtype(categories=sorted_cats)
            
        return self
        
    def transform(self, examples: list):
        """
        Transforms raw dictionaries into a pandas DataFrame.
        Applies the fitted CategoricalDtype to enforce vocabulary integrity.
        """
        if not self.categorical_dtypes:
            raise ValueError("Preprocessor has not been fitted.")
            
        rows = []
        labels = []
        example_ids = []
        
        for ex in examples:
            features = ex["features"]
            row = {}
            
            # Categorical
            for cat_feat in self.categorical_features:
                val = features.get(cat_feat)
                row[cat_feat] = str(val) if val is not None else "UNKNOWN"
                
            # Boolean
            for bool_feat in self.boolean_features:
                val = features.get(bool_feat)
                if val is None:
                    row[bool_feat] = np.nan
                else:
                    row[bool_feat] = 1.0 if val else 0.0
                    
            # Numerical
            for num_feat in self.numerical_features:
                val = features.get(num_feat)
                row[num_feat] = float(val) if val is not None else np.nan
                
            rows.append(row)
            
            # Extract target and ID if present (used for returning downstream structures)
            if "label" in ex:
                labels.append(ex["label"])
            if "example_id" in ex:
                example_ids.append(ex["example_id"])
                
        df = pd.DataFrame(rows)
        
        # Enforce column order (categorical, boolean, numerical)
        ordered_cols = self.categorical_features + self.boolean_features + self.numerical_features
        df = df[ordered_cols]
        
        # Cast categorical columns to the fitted CategoricalDtype
        # Any string not in the fitted categories will safely become NaN
        for cat_feat in self.categorical_features:
            df[cat_feat] = df[cat_feat].astype(self.categorical_dtypes[cat_feat])
            
        # Ensure no forbidden features are present
        forbidden_fields = [
            'case_id', 'example_id', 'dispute_id', 'document_id', 'label',
            'label_rationale', 'label_schema_version', 'label_policy_version',
            'scenario_family', 'synthetic_customer_group', 'feature_hash',
            'prediction_timestamp'
        ]
        for f in forbidden_fields:
            if f in df.columns:
                raise ValueError(f"Leakage detected: {f} found in transformed feature matrix.")
                
        y = np.array(labels) if labels else None
        
        return df, y, example_ids
