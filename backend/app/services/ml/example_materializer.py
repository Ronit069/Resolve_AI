import dataclasses
import json
import hashlib
from typing import Dict, Any
from datetime import datetime
from uuid import UUID

from app.services.ml.feature_builder import build_ml_features, FeatureBuilderContext, MLFeaturesV1
from app.services.ml.label_policy import generate_contestability_label, LabelContext, LabelRationale
from app.models.module_e import CaseFeatureSnapshot


@dataclasses.dataclass
class MLExample:
    feature_schema_version: str
    features: Dict[str, Any]
    feature_hash: str
    label_schema_version: str
    label: int
    label_rationale: Dict[str, Any]
    case_id: str
    prediction_timestamp: datetime


def _compute_feature_hash(features_dict: Dict[str, Any]) -> str:
    """
    Computes a deterministic SHA-256 hash of the feature dictionary.
    """
    canonical_json = json.dumps(features_dict, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()


def materialize_example_from_context(
    feature_context: FeatureBuilderContext,
    prediction_timestamp: datetime
) -> MLExample:
    """
    Materializes a complete ML example deterministically directly from A-E context.
    Features and labels are computed independently.
    """
    # 1. Build Features
    ml_features = build_ml_features(feature_context, prediction_timestamp)
    features_dict = dataclasses.asdict(ml_features)
    
    # Pop version so it sits at the top level of the example
    feature_schema_version = features_dict.pop("version")
    feature_hash = _compute_feature_hash(features_dict)

    # 2. Build Label
    label_context = LabelContext(
        case=feature_context.case,
        dispute=feature_context.dispute,
        assessments=feature_context.assessments,
        validation_results=feature_context.results,
        quality_assessments=feature_context.quality_assessments,
        extractions=feature_context.extractions
    )
    rationale = generate_contestability_label(label_context, prediction_timestamp)

    # 3. Assemble Immutable Example
    return MLExample(
        feature_schema_version=feature_schema_version,
        features=features_dict,
        feature_hash=feature_hash,
        label_schema_version=rationale.label_policy_version,
        label=rationale.label,
        label_rationale=dataclasses.asdict(rationale),
        case_id=str(feature_context.case.case_id),
        prediction_timestamp=prediction_timestamp
    )


def materialize_example_from_snapshot(
    snapshot: CaseFeatureSnapshot,
    label_context: LabelContext,
    prediction_timestamp: datetime
) -> MLExample:
    """
    Materializes an ML example using an existing historical CaseFeatureSnapshot as the canonical 
    feature truth, while generating the deterministic label alongside it.
    """
    # Use features strictly from the canonical snapshot
    features_dict = dict(snapshot.features_json)
    
    # If the snapshot stored 'version' inside the JSON, pop it
    version = features_dict.pop("version", snapshot.feature_schema_version)

    # We assume snapshot.feature_hash already exists, but we can re-verify if needed.
    feature_hash = snapshot.feature_hash or _compute_feature_hash(features_dict)

    # Generate deterministic label based on context at prediction_timestamp
    rationale = generate_contestability_label(label_context, prediction_timestamp)

    return MLExample(
        feature_schema_version=version,
        features=features_dict,
        feature_hash=feature_hash,
        label_schema_version=rationale.label_policy_version,
        label=rationale.label,
        label_rationale=dataclasses.asdict(rationale),
        case_id=str(snapshot.case_id),
        prediction_timestamp=prediction_timestamp
    )
