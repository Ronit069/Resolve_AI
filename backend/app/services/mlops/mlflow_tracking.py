"""
Module L, L-01/L-02/L-03 — MLflow experiment tracking helper.

Additive, logging-only wiring around the existing training scripts
(train_lightgbm_comparator.py etc., at the repo root). Never changes
what those scripts compute — a training run must succeed identically
whether or not an MLflow tracking server is reachable. Logging failures
are caught and logged, never raised, so this can never break a training
script's actual (frozen) behavior.

Local/self-hosted only (frozen PO decision): MLFLOW_TRACKING_URI
defaults to a local file store (./mlruns) via app.core.config.settings,
so nothing here requires an external/hosted MLflow service or any
production credential. The optional `mlflow` docker-compose service, if
running, can be pointed at via that same setting.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _flatten_numeric(d: Dict[str, Any], prefix: str = "") -> Dict[str, float]:
    flat: Dict[str, float] = {}
    for key, value in d.items():
        full_key = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten_numeric(value, prefix=f"{full_key}."))
        elif isinstance(value, bool):
            continue
        elif isinstance(value, (int, float)):
            flat[full_key] = float(value)
    return flat


def log_training_run(
    experiment_name: str,
    run_name: str,
    params: Dict[str, Any],
    metrics: Dict[str, Any],
    tags: Optional[Dict[str, str]] = None,
    artifact_dir: Optional[str] = None,
) -> None:
    """
    Best-effort MLflow logging for one training run (L-01: params/metrics;
    L-02: connects to the model/version concept via tags; L-03:
    dataset/version trace via params/tags). Never raises.
    """
    try:
        import mlflow

        from app.core.config import settings

        mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
        mlflow.set_experiment(experiment_name)

        with mlflow.start_run(run_name=run_name):
            if tags:
                mlflow.set_tags(tags)
            for key, value in params.items():
                mlflow.log_param(key, value)
            for key, value in _flatten_numeric(metrics).items():
                mlflow.log_metric(key, value)
            if artifact_dir:
                mlflow.log_artifacts(artifact_dir)
    except Exception:
        logger.warning(
            "MLflow logging failed for run '%s' (experiment '%s'); continuing without tracking.",
            run_name, experiment_name, exc_info=True,
        )
