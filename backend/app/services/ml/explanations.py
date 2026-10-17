"""
Module F — SHAP-based prediction explanations.

Computes each feature's CatBoost SHAP contribution to a single live
prediction's raw (margin-space) score, and ranks the top contributors
for display. Never recomputes or second-guesses the prediction itself
-- this only explains a score already produced by
app/services/ml/inference.py::score_case, and a failure here is caught
by the caller so it never blocks that prediction from being persisted.
"""

import logging
from typing import Any, Dict, List

import pandas as pd
from catboost import CatBoostClassifier, Pool

logger = logging.getLogger(__name__)

TOP_N_EXPLANATIONS = 5

# Human-readable labels for ml_features_v1 -- see
# app/services/ml/feature_builder.py::MLFeaturesV1 for the authoritative
# field definitions this mirrors.
_FEATURE_LABELS: Dict[str, str] = {
    "reason_code": "Dispute reason code",
    "payment_method": "Payment method",
    "amount_match": "Amount matches evidence",
    "order_id_match": "Order ID matches evidence",
    "tracking_match": "Tracking number matches evidence",
    "timeline_valid": "Delivery timeline is valid",
    "refund_exists": "A refund already exists",
    "shipment_available": "Shipment evidence is available",
    "required_evidence_coverage": "Required evidence coverage",
    "missing_required_count": "Missing required evidence items",
    "evidence_count": "Evidence documents submitted",
    "customer_match_score": "Customer identity match score",
    "days_delivery_to_dispute": "Days from delivery to dispute",
    "contradiction_count": "Contradictions found in evidence",
    "high_severity_contradictions": "High-severity contradictions",
    "avg_ocr_confidence": "Average OCR confidence",
    "min_ocr_confidence": "Minimum OCR confidence",
    "document_quality_score": "Document quality score",
    "dispute_amount": "Dispute amount",
    "disputed_amount_ratio": "Disputed amount ratio",
    "days_to_deadline": "Days remaining to respond",
}


def _format_feature_value(value: Any) -> str:
    if value is None:
        return "missing"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}" if not value.is_integer() else str(int(value))
    return str(value)


def _direction_for(contribution: float) -> str:
    if contribution > 0:
        return "INCREASES_RISK"
    if contribution < 0:
        return "DECREASES_RISK"
    return "NEUTRAL"


_DIRECTION_WORDS = {"INCREASES_RISK": "raises risk", "DECREASES_RISK": "lowers risk", "NEUTRAL": "neutral"}


def compute_shap_explanations(
    model: CatBoostClassifier,
    X: pd.DataFrame,
    categorical_features: List[str],
    raw_features: Dict[str, Any],
    top_n: int = TOP_N_EXPLANATIONS,
) -> List[Dict[str, Any]]:
    """
    Returns up to top_n dicts {rank, feature_name, feature_value,
    contribution, direction, display_text}, ranked by |contribution|
    descending.

    X must be the single-row DataFrame the model was just scored with
    (the same Preprocessor.transform() output score_case() built).
    raw_features is the snapshot's original ml_features_v1 dict, used
    only for a human-readable feature_value -- X holds the
    imputed/encoded values the model actually saw, which aren't what a
    reviewer wants displayed (e.g. a missing boolean becomes NaN there).
    """
    cat_indices = [X.columns.get_loc(c) for c in categorical_features]
    pool = Pool(X, cat_features=cat_indices)
    shap_values = model.get_feature_importance(pool, type="ShapValues")
    contributions = shap_values[0][:-1]  # drop the trailing bias/expected-value term

    ranked = sorted(zip(X.columns, contributions), key=lambda pair: abs(pair[1]), reverse=True)

    explanations = []
    for rank, (feature_name, contribution) in enumerate(ranked[:top_n], start=1):
        contribution = float(contribution)
        direction = _direction_for(contribution)
        value = raw_features.get(feature_name)
        label = _FEATURE_LABELS.get(feature_name, feature_name)
        explanations.append({
            "rank": rank,
            "feature_name": feature_name,
            "feature_value": value,
            "contribution": contribution,
            "direction": direction,
            "display_text": f"{label} = {_format_feature_value(value)} ({_DIRECTION_WORDS[direction]})",
        })
    return explanations
