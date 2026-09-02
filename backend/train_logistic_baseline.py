import os
import json
import hashlib
import numpy as np
import pandas as pd
from datetime import datetime, timezone
import joblib

from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_score, recall_score, f1_score, average_precision_score, roc_auc_score, confusion_matrix

from app.services.ml.training.cost_policy import calculate_expected_cost

def hash_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def hash_dataset(data: list) -> str:
    h = hashlib.sha256()
    for row in data:
        h.update(json.dumps(row, sort_keys=True).encode("utf-8"))
    return h.hexdigest()

def load_data(filepath: str):
    if "test_holdout" in filepath.lower():
        raise PermissionError("Access to TEST_HOLDOUT is strictly forbidden.")
        
    data = []
    with open(filepath, "r") as f:
        for line in f:
            if line.strip():
                data.append(json.loads(line))
    return data

def extract_features(data):
    categorical_features = ['reason_code', 'payment_method']
    boolean_features = [
        'amount_match', 'order_id_match', 'tracking_match', 'timeline_valid',
        'refund_exists', 'shipment_available'
    ]
    numerical_features = [
        'required_evidence_coverage', 'missing_required_count', 'evidence_count',
        'customer_match_score', 'days_delivery_to_dispute', 'contradiction_count',
        'high_severity_contradictions', 'avg_ocr_confidence', 'min_ocr_confidence',
        'document_quality_score', 'dispute_amount', 'disputed_amount_ratio',
        'days_to_deadline'
    ]
    
    rows = []
    labels = []
    example_ids = []
    
    for row in data:
        example_ids.append(row["example_id"])
        labels.append(row["label"])
        
        # We explicitly whitelist from the JSON, ignoring all other metadata
        features_dict = row["features"]
        extracted = {}
        
        for f in categorical_features:
            val = features_dict.get(f)
            extracted[f] = str(val) if val is not None else np.nan
            
        for f in boolean_features:
            val = features_dict.get(f)
            if val is None:
                extracted[f] = np.nan
            else:
                extracted[f] = 1.0 if val else 0.0
                
        for f in numerical_features:
            val = features_dict.get(f)
            extracted[f] = float(val) if val is not None else np.nan
            
        rows.append(extracted)
        
    df = pd.DataFrame(rows)
    # Enforce column order
    ordered_cols = categorical_features + boolean_features + numerical_features
    df = df[ordered_cols]
    
    # Check for forbidden fields sneaking in
    forbidden = ['case_id', 'example_id', 'label', 'label_rationale']
    for f in forbidden:
        if f in df.columns:
            raise ValueError(f"Leakage detected: {f} in feature matrix.")
            
    return df, np.array(labels), example_ids

def build_pipeline(numeric_cols, cat_cols, bool_cols):
    numeric_transformer = Pipeline(steps=[
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler', StandardScaler())
    ])

    categorical_transformer = Pipeline(steps=[
        ('imputer', SimpleImputer(strategy='constant', fill_value='UNKNOWN')),
        ('onehot', OneHotEncoder(handle_unknown='ignore', sparse_output=False))
    ])
    
    boolean_transformer = Pipeline(steps=[
        ('imputer', SimpleImputer(strategy='constant', fill_value=-1.0)),
        ('scaler', StandardScaler())
    ])

    preprocessor = ColumnTransformer(
        transformers=[
            ('cat', categorical_transformer, cat_cols),
            ('bool', boolean_transformer, bool_cols),
            ('num', numeric_transformer, numeric_cols)
        ]
    )
    
    return preprocessor

def main():
    train_file = "synthetic_benchmark_v1_train.jsonl"
    val_file = "synthetic_benchmark_v1_validation.jsonl"
    
    print("Loading data...")
    train_data = load_data(train_file)
    val_data = load_data(val_file)
    
    X_train, y_train, _ = extract_features(train_data)
    X_val, y_val, val_example_ids = extract_features(val_data)
    
    # Determine Class Imbalance from TRAIN only
    pos_count = np.sum(y_train == 1)
    neg_count = np.sum(y_train == 0)
    total = len(y_train)
    min_class_ratio = min(pos_count, neg_count) / total
    
    class_weight = None
    imbalance_rationale = f"Minority class ratio: {min_class_ratio:.4f}."
    if min_class_ratio < 0.20: # Just a heuristic, but we'll document it properly
        class_weight = "balanced"
        imbalance_rationale += " Material imbalance detected, applying class_weight='balanced'."
    else:
        imbalance_rationale += " Imbalance not material, using default weights."
        
    print(imbalance_rationale)
        
    # Preprocessing
    categorical_features = ['reason_code', 'payment_method']
    boolean_features = [
        'amount_match', 'order_id_match', 'tracking_match', 'timeline_valid',
        'refund_exists', 'shipment_available'
    ]
    numerical_features = [
        'required_evidence_coverage', 'missing_required_count', 'evidence_count',
        'customer_match_score', 'days_delivery_to_dispute', 'contradiction_count',
        'high_severity_contradictions', 'avg_ocr_confidence', 'min_ocr_confidence',
        'document_quality_score', 'dispute_amount', 'disputed_amount_ratio',
        'days_to_deadline'
    ]
    
    preprocessor = build_pipeline(numerical_features, categorical_features, boolean_features)
    
    # Grid Search on C using VALIDATION
    C_grid = [0.01, 0.1, 1.0, 10.0]
    best_c = None
    best_pr_auc = -1
    best_model = None
    
    print("Training grid...")
    for c in C_grid:
        clf = Pipeline(steps=[
            ('preprocessor', preprocessor),
            ('classifier', LogisticRegression(C=c, class_weight=class_weight, random_state=42, max_iter=1000))
        ])
        
        clf.fit(X_train, y_train)
        y_val_prob = clf.predict_proba(X_val)[:, 1]
        
        pr_auc = average_precision_score(y_val, y_val_prob)
        if pr_auc > best_pr_auc:
            best_pr_auc = pr_auc
            best_c = c
            best_model = clf
            
    print(f"Selected C={best_c} with PR-AUC={best_pr_auc:.4f}")
    
    # Validation Evaluation
    y_val_prob = best_model.predict_proba(X_val)[:, 1]
    y_val_pred = (y_val_prob >= 0.5).astype(int)
    
    precision = precision_score(y_val, y_val_pred, zero_division=0)
    recall = recall_score(y_val, y_val_pred, zero_division=0)
    f1 = f1_score(y_val, y_val_pred, zero_division=0)
    pr_auc = average_precision_score(y_val, y_val_prob)
    roc_auc = roc_auc_score(y_val, y_val_prob)
    tn, fp, fn, tp = confusion_matrix(y_val, y_val_pred).ravel()
    
    # Cost Policy Diagnostic
    c_fp = float(os.environ.get("C_FP", 50.0))
    c_fn = float(os.environ.get("C_FN", 100.0))
    c_review = float(os.environ.get("C_REVIEW", 5.0))
    
    cost = calculate_expected_cost(
        pd.Series(y_val), 
        pd.Series(y_val_prob), 
        threshold=0.5, 
        c_fp=c_fp, c_fn=c_fn, c_review=c_review
    )
    
    metrics = {
        "precision": float(precision),
        "recall": float(recall),
        "f1_score": float(f1),
        "pr_auc": float(pr_auc),
        "roc_auc": float(roc_auc),
        "confusion_matrix": {
            "TP": int(tp), "TN": int(tn), "FP": int(fp), "FN": int(fn)
        },
        "diagnostic_cost_at_0.5": cost
    }
    
    # Interpretability
    transformer = best_model.named_steps['preprocessor']
    classifier = best_model.named_steps['classifier']
    
    feature_names = transformer.get_feature_names_out()
    coefficients = classifier.coef_[0]
    
    coef_list = []
    for name, coef in zip(feature_names, coefficients):
        direction = "Increases Safety" if coef > 0 else "Decreases Safety"
        if coef == 0:
            direction = "Neutral"
            
        coef_list.append({
            "feature_name": name,
            "coefficient": float(coef),
            "sign": "Positive" if coef > 0 else ("Negative" if coef < 0 else "Zero"),
            "absolute_magnitude": float(abs(coef)),
            "direction": direction
        })
        
    coef_df = pd.DataFrame(coef_list)
    
    # Output Probabilities
    prob_df = pd.DataFrame({
        "example_id": val_example_ids,
        "p_safe_to_contest": y_val_prob,
        "true_label": y_val
    })
    
    # Artifacts
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join("artifacts", f"logistic_baseline_{timestamp}_v1")
    os.makedirs(out_dir, exist_ok=True)
    
    meta = {
        "model": "Logistic Regression Baseline",
        "training_seed": 42,
        "class_imbalance_rationale": imbalance_rationale,
        "selected_hyperparameters": {
            "C": best_c,
            "class_weight": class_weight
        },
        "validation_after_model_selection": metrics,
        "provenance": {
            "train_hash": hash_dataset(train_data),
            "validation_hash": hash_dataset(val_data)
        },
        "cost_configuration": {
            "c_fp": c_fp, "c_fn": c_fn, "c_review": c_review
        }
    }
    
    with open(os.path.join(out_dir, "metadata_logistic.json"), "w") as f:
        json.dump(meta, f, indent=2)
        
    coef_df.to_csv(os.path.join(out_dir, "logistic_coefficients.csv"), index=False)
    prob_df.to_csv(os.path.join(out_dir, "validation_probabilities.csv"), index=False)
    joblib.dump(best_model, os.path.join(out_dir, "logistic_model.joblib"))
    
    print(f"Done. Artifacts saved to {out_dir}")
    
if __name__ == "__main__":
    main()
