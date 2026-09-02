import catboost
from typing import Dict, Any, List
import pandas as pd
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score, average_precision_score, confusion_matrix
from app.services.ml.training.cost_policy import calculate_expected_cost

class CatBoostTrainer:
    def __init__(self, categorical_features: List[str]):
        self.categorical_features = categorical_features
        self.model = None
        self.seed = 42
        self.best_iteration = None

    def train(self, X_train: pd.DataFrame, y_train: pd.Series, X_val: pd.DataFrame, y_val: pd.Series):
        # We explicitly configure nan_mode="Min" so missing numerical values are treated as a distinct group 
        # below all other values, giving the tree the ability to split on missingness natively.
        self.model = catboost.CatBoostClassifier(
            iterations=1000,
            auto_class_weights="Balanced",
            random_seed=self.seed,
            eval_metric="PRAUC", # Primary ranking metric
            early_stopping_rounds=50,
            nan_mode="Min",
            cat_features=self.categorical_features,
            verbose=False
        )

        self.model.fit(
            X_train, y_train,
            eval_set=(X_val, y_val)
        )
        self.best_iteration = self.model.get_best_iteration()

    def evaluate(self, X_val: pd.DataFrame, y_val: pd.Series, c_fp: float = 50.0, c_fn: float = 100.0, c_review: float = 5.0) -> Dict[str, Any]:
        """
        Calculates validation metrics.
        """
        y_pred = self.model.predict(X_val)
        # CatBoost predict() might return string '0' / '1' if labels are inferred poorly, 
        # but since we pass numeric 0/1 it should return integers.
        y_pred = y_pred.astype(int)
        y_pred_prob = self.model.predict_proba(X_val)[:, 1]
        
        cm = confusion_matrix(y_val, y_pred)
        
        # Expected Cost as a diagnostic metric at fixed threshold 0.5
        cost = calculate_expected_cost(
            y_true=y_val,
            y_pred_prob=pd.Series(y_pred_prob),
            threshold=0.5,
            c_fp=c_fp,
            c_fn=c_fn,
            c_review=c_review
        )
        
        return {
            "accuracy": float(accuracy_score(y_val, y_pred)),
            "precision": float(precision_score(y_val, y_pred)),
            "recall": float(recall_score(y_val, y_pred)),
            "f1": float(f1_score(y_val, y_pred)),
            "roc_auc": float(roc_auc_score(y_val, y_pred_prob)),
            "pr_auc": float(average_precision_score(y_val, y_pred_prob)),
            "expected_cost_diagnostic": cost,
            "confusion_matrix": {
                "tn": int(cm[0,0]),
                "fp": int(cm[0,1]),
                "fn": int(cm[1,0]),
                "tp": int(cm[1,1])
            }
        }
        
    def save(self, filepath: str):
        if self.model:
            self.model.save_model(filepath)
