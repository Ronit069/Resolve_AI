"""
Module F — live champion model loader (read-only).

Loads the frozen CatBoost model + isotonic calibrator + locked 3-way
decision policy that the offline pipeline (train_benchmark.py ->
calibrate_winner_step13.py -> optimize_thresholds_step14.py) already
produced under artifacts/. Never trains, calibrates, re-optimizes, or
touches test_holdout -- mirrors the same "offline script writes
artifacts/, app/ reads them" convention as evaluation_artifact.py, for
the model/calibrator/policy triad a live scorer needs instead of the
Step 15 evaluation result.
"""

import hashlib
import json
import os
import pickle
from dataclasses import dataclass
from typing import Optional

import catboost

from app.core.config import settings


class ChampionArtifactsUnavailable(Exception):
    """Raised when no trained champion model/calibrator/policy exists on disk yet."""


def _get_latest_dir(parent_dir: str, match_str: str, exclude_str: Optional[str] = None) -> Optional[str]:
    if not os.path.isdir(parent_dir):
        return None
    dirs = [d for d in os.listdir(parent_dir) if match_str in d]
    if exclude_str:
        dirs = [d for d in dirs if exclude_str not in d]
    if not dirs:
        return None
    # Timestamp-named directories sort lexicographically = chronologically,
    # the same convention get_latest_dir() uses throughout the pipeline.
    return sorted(dirs)[-1]


@dataclass
class ChampionArtifacts:
    model: "catboost.CatBoostClassifier"
    calibrator: object
    model_sha256: str
    t_accept: float
    t_contest: float
    c_fp: float
    c_fn: float
    c_review: float
    model_run_dir: str
    model_cbm_path: str
    calibrator_path: str
    calibration_dir: str
    policy_dir: str


def load_champion_artifacts() -> ChampionArtifacts:
    """
    Loads the latest champion model/calibrator/policy from disk. Raises
    ChampionArtifactsUnavailable -- never returns a fabricated/default
    model -- if any piece is missing on this machine.
    """
    artifacts_dir = settings.MODEL_EVALUATION_ARTIFACT_DIR
    runs_dir = os.path.join(artifacts_dir, "runs")

    f6_dir = _get_latest_dir(runs_dir, "", exclude_str="calibrated")
    f13_dir = _get_latest_dir(artifacts_dir, "step13_calibration")
    f14_dir = _get_latest_dir(artifacts_dir, "step14_threshold_policy")

    if not (f6_dir and f13_dir and f14_dir):
        raise ChampionArtifactsUnavailable(
            "No trained champion model/calibrator/policy found under "
            f"'{artifacts_dir}/'. Run the offline pipeline (train_benchmark.py -> "
            "calibrate_winner_step13.py -> optimize_thresholds_step14.py) on this "
            "machine, or deploy its artifacts/, before live risk scoring can run."
        )

    model_path = os.path.join(runs_dir, f6_dir, "model.cbm")
    calibrator_path = os.path.join(artifacts_dir, f13_dir, "calibrator.pkl")
    policy_path = os.path.join(artifacts_dir, f14_dir, "decision_policy.json")

    if not (os.path.isfile(model_path) and os.path.isfile(calibrator_path) and os.path.isfile(policy_path)):
        raise ChampionArtifactsUnavailable(
            f"Latest artifact directories found ({f6_dir}, {f13_dir}, {f14_dir}) under "
            f"'{artifacts_dir}/' but one or more expected files are missing."
        )

    hasher = hashlib.sha256()
    with open(model_path, "rb") as f:
        hasher.update(f.read())
    model_sha256 = hasher.hexdigest()

    model = catboost.CatBoostClassifier()
    model.load_model(model_path)

    with open(calibrator_path, "rb") as f:
        calibrator = pickle.load(f)

    with open(policy_path) as f:
        policy = json.load(f)

    return ChampionArtifacts(
        model=model,
        calibrator=calibrator,
        model_sha256=model_sha256,
        t_accept=float(policy["t_accept"]),
        t_contest=float(policy["t_contest"]),
        c_fp=float(policy["c_fp"]),
        c_fn=float(policy["c_fn"]),
        c_review=float(policy["c_review"]),
        model_run_dir=f6_dir,
        model_cbm_path=model_path,
        calibrator_path=calibrator_path,
        calibration_dir=f13_dir,
        policy_dir=f14_dir,
    )
