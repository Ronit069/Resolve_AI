"""
Module L — authoritative Step 15 held-out evaluation artifact loader.

Read-only. Loads the most recent
artifacts/step15_final_holdout_eval_*/final_evaluation.json written by
the repo-root evaluate_holdout_step15.py script — the one-time,
authorized evaluation of the frozen champion model + validation-only
calibrator + validation-only locked decision policy against the
previously untouched test_holdout split.

This module never recomputes, retrains, or runs inference, and never
fabricates placeholder metrics. If no artifact exists yet, callers get
EvaluationArtifactUnavailable and must report that honestly rather than
substitute zeros — mirroring the same "repo-root script writes
artifacts/, app/ reads them" convention already used by
mlflow_tracking.log_training_run's artifact_dir and
model_registry's ModelVersion.artifact_uri.
"""

import glob
import json
import os
from typing import Any, Dict

from app.core.config import settings

ARTIFACT_GLOB_PATTERN = os.path.join("step15_final_holdout_eval_*", "final_evaluation.json")


class EvaluationArtifactUnavailable(Exception):
    """Raised when no authoritative Step 15 evaluation artifact exists on disk."""


def _find_latest_evaluation_file() -> str:
    pattern = os.path.join(settings.MODEL_EVALUATION_ARTIFACT_DIR, ARTIFACT_GLOB_PATTERN)
    # Timestamp-named directories sort lexicographically = chronologically,
    # the same "sorted(...)[-1]" convention used by get_latest_dir() in the
    # pipeline scripts (optimize_thresholds_step14.py, calibrate_winner_step13.py).
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        raise EvaluationArtifactUnavailable(
            "No authoritative Step 15 evaluation artifact found "
            f"(looked for {pattern}). Run evaluate_holdout_step15.py first."
        )
    return candidates[-1]


def load_latest_evaluation() -> Dict[str, Any]:
    """
    Returns the parsed contents of the most recent Step 15
    final_evaluation.json. Raises EvaluationArtifactUnavailable if none
    exists — callers must surface that honestly, never substitute
    fabricated/placeholder metrics.
    """
    path = _find_latest_evaluation_file()
    with open(path) as f:
        return json.load(f)
