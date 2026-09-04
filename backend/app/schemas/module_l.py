from typing import Dict, Optional

from pydantic import BaseModel


class RuntimeMetricCategory(BaseModel):
    sample_count: int
    error_count: int
    avg_latency_ms: Optional[float] = None
    min_latency_ms: Optional[float] = None
    max_latency_ms: Optional[float] = None
    error_rate: Optional[float] = None


class RuntimeMetricsResponse(BaseModel):
    categories: Dict[str, RuntimeMetricCategory]


# ---- Authoritative Step 15 held-out evaluation (read-only exposure) ----

class EvaluationConfusionMatrix(BaseModel):
    tp: int
    tn: int
    fp: int
    fn: int


class ModelEvaluationModelProvenance(BaseModel):
    algorithm: str
    run_id: str
    model_sha256: str


class ModelEvaluationProvenance(BaseModel):
    holdout_file: str
    holdout_sha256: str
    evaluation_timestamp: str
    calibration_method: Optional[str] = None
    policy_id: str


class ModelEvaluationResponse(BaseModel):
    """
    Read-only exposure of the authoritative Step 15 held-out evaluation
    artifact. Every field here is copied verbatim from
    final_evaluation.json — this schema never computes a metric itself.
    """
    sample_count: int
    positive_count: int
    negative_count: int
    precision: float
    recall: float
    f1: float
    accuracy: float
    confusion_matrix: EvaluationConfusionMatrix
    false_positive_count: int
    expected_cost: float
    accept_count: int
    review_count: int
    contest_count: int
    brier_raw: float
    brier_calibrated: float
    model: ModelEvaluationModelProvenance
    evaluation: ModelEvaluationProvenance
