"""
Module F / L — champion model loading.

Two distinct reads, kept separate on purpose:

  load_candidate_artifacts_from_disk() -- scans artifacts/ for the latest
  model/calibrator/policy (and, if present, step15 holdout evaluation)
  written by the offline pipeline. This is "what's sitting on disk",
  never what live traffic is scored with.

  load_champion_artifacts(db) -- loads the exact model/calibrator/policy
  files referenced by the registry's current active ModelVersion /
  ModelDecisionPolicy rows. This is "what live traffic is scored with",
  and never falls back to "latest on disk" -- a new candidate only
  becomes live once model_registry.promote_candidate_from_disk() has
  evaluated and approved it.

Neither function trains, calibrates, re-optimizes, or touches
test_holdout -- mirrors the same "offline script writes artifacts/,
app/ reads them" convention as evaluation_artifact.py.
"""

import hashlib
import json
import logging
import os
import pickle
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import catboost
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)


class ChampionArtifactsUnavailable(Exception):
    """Raised when no trained/promoted champion model/calibrator/policy is available."""


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


def _load_holdout_metrics(artifacts_dir: str, model_run_dir: str, policy_run_dir: str) -> Optional[dict]:
    """
    Best-effort read of the step15 final_evaluation.json, if the offline
    pipeline has run it for exactly this model_run_dir/policy_run_dir
    pair. Returns None (not an error) when it hasn't -- step15 is a
    one-time, explicitly-authorized step and isn't required for every
    model that ever lands on disk. The latest step15 directory present
    is only trusted when it names THIS model run and THIS policy run as
    what it evaluated; otherwise it's a stale eval of a previous model
    still sitting on disk, and treating it as current would let an
    untested retrain look already-verified.
    """
    f15_dir = _get_latest_dir(artifacts_dir, "step15_final_holdout_eval")
    if not f15_dir:
        return None
    eval_path = os.path.join(artifacts_dir, f15_dir, "final_evaluation.json")
    if not os.path.isfile(eval_path):
        return None
    with open(eval_path) as f:
        evaluation = json.load(f)

    evaluated_model_run_dir = evaluation.get("champion_model", {}).get("run_dir")
    evaluated_policy_run_dir = evaluation.get("decision_policy", {}).get("step14_dir")
    if evaluated_model_run_dir != model_run_dir or evaluated_policy_run_dir != policy_run_dir:
        return None

    metrics = evaluation.get("metrics", {})
    policy = evaluation.get("decision_policy", {})
    return {
        "expected_cost": metrics.get("expected_cost"),
        "precision": metrics.get("precision"),
        "recall": metrics.get("recall"),
        "f1": metrics.get("f1"),
        "brier_score_calibrated": metrics.get("brier_score_calibrated"),
        "c_fp": policy.get("c_fp"),
        "c_fn": policy.get("c_fn"),
        "c_review": policy.get("c_review"),
        "holdout_eval_dir": f15_dir,
    }


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
    holdout_metrics: Optional[dict] = None
    model_version_id: Optional[UUID] = None
    decision_policy_id: Optional[UUID] = None


def _load_artifacts_from_paths(model_path: str, calibrator_path: str, policy: dict) -> ChampionArtifacts:
    hasher = hashlib.sha256()
    with open(model_path, "rb") as f:
        hasher.update(f.read())
    model_sha256 = hasher.hexdigest()

    model = catboost.CatBoostClassifier()
    model.load_model(model_path)

    with open(calibrator_path, "rb") as f:
        calibrator = pickle.load(f)

    return ChampionArtifacts(
        model=model,
        calibrator=calibrator,
        model_sha256=model_sha256,
        t_accept=float(policy["t_accept"]),
        t_contest=float(policy["t_contest"]),
        c_fp=float(policy["c_fp"]),
        c_fn=float(policy["c_fn"]),
        c_review=float(policy["c_review"]),
        model_run_dir=os.path.dirname(model_path),
        model_cbm_path=model_path,
        calibrator_path=calibrator_path,
        calibration_dir=policy.get("_calibration_dir", ""),
        policy_dir=policy.get("_policy_dir", ""),
    )


def load_candidate_artifacts_from_disk() -> ChampionArtifacts:
    """
    Loads the latest model/calibrator/policy found under artifacts/ on
    this machine -- a candidate for promotion, NOT what live scoring
    uses. Raises ChampionArtifactsUnavailable if any piece is missing.
    """
    artifacts_dir = settings.MODEL_EVALUATION_ARTIFACT_DIR
    runs_dir = os.path.join(artifacts_dir, "runs")

    f6_dir = _get_latest_dir(runs_dir, "", exclude_str="calibrated")
    f13_dir = _get_latest_dir(artifacts_dir, "step13_calibration")
    f14_dir = _get_latest_dir(artifacts_dir, "step14_threshold_policy")

    if not (f6_dir and f13_dir and f14_dir):
        raise ChampionArtifactsUnavailable(
            "No trained candidate model/calibrator/policy found under "
            f"'{artifacts_dir}/'. Run the offline pipeline (train_benchmark.py -> "
            "calibrate_winner_step13.py -> optimize_thresholds_step14.py) on this "
            "machine, or deploy its artifacts/, before a champion can be promoted."
        )

    model_path = os.path.join(runs_dir, f6_dir, "model.cbm")
    calibrator_path = os.path.join(artifacts_dir, f13_dir, "calibrator.pkl")
    policy_path = os.path.join(artifacts_dir, f14_dir, "decision_policy.json")

    if not (os.path.isfile(model_path) and os.path.isfile(calibrator_path) and os.path.isfile(policy_path)):
        raise ChampionArtifactsUnavailable(
            f"Latest artifact directories found ({f6_dir}, {f13_dir}, {f14_dir}) under "
            f"'{artifacts_dir}/' but one or more expected files are missing."
        )

    with open(policy_path) as f:
        policy = json.load(f)
    policy["_calibration_dir"] = f13_dir
    policy["_policy_dir"] = f14_dir

    artifacts = _load_artifacts_from_paths(model_path, calibrator_path, policy)
    artifacts.holdout_metrics = _load_holdout_metrics(artifacts_dir, f6_dir, f14_dir)
    return artifacts


def _object_storage_key(model_version_id: UUID, filename: str) -> str:
    return f"models/{model_version_id}/{filename}"


def _download_cached(key: str) -> str:
    """
    Downloads an object-storage key to a local cache file (under
    MODEL_EVALUATION_ARTIFACT_DIR/_object_storage_cache/<key>) and
    returns its path, skipping the download if already cached. Safe to
    cache indefinitely: each ModelVersion's artifacts are written to
    this key exactly once, at promotion time, and never overwritten.
    """
    from app.core.storage import storage_client

    cache_path = os.path.join(settings.MODEL_EVALUATION_ARTIFACT_DIR, "_object_storage_cache", key)
    if os.path.isfile(cache_path):
        return cache_path

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    data = storage_client.download_file(key)
    tmp_path = f"{cache_path}.tmp.{os.getpid()}"
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, cache_path)
    return cache_path


def upload_champion_artifacts_to_object_storage(candidate: ChampionArtifacts, model_version_id: UUID) -> None:
    """
    Best-effort upload of a newly-promoted champion's model.cbm and
    calibrator.pkl to object storage, at the same deterministic key
    load_champion_artifacts() falls back to when its local artifact_uri
    path doesn't exist on the current machine -- this is what lets a
    model promoted on the training machine actually get loaded by a
    separate production machine, instead of only ever working on
    whichever disk the offline pipeline happened to write to.

    Never raises: a storage hiccup here must not block promotion itself,
    since this machine can still serve the just-promoted model from its
    own local disk either way.
    """
    from app.core.storage import storage_client

    try:
        with open(candidate.model_cbm_path, "rb") as f:
            storage_client.upload_file(
                f, _object_storage_key(model_version_id, "model.cbm"), content_type="application/octet-stream"
            )
        with open(candidate.calibrator_path, "rb") as f:
            storage_client.upload_file(
                f, _object_storage_key(model_version_id, "calibrator.pkl"), content_type="application/octet-stream"
            )
    except Exception as exc:
        logger.warning(
            f"Failed to upload champion artifacts for ModelVersion {model_version_id} to object "
            f"storage; this machine will still serve it from local disk, but a different machine "
            f"won't be able to load it until this is retried: {exc}"
        )


def load_champion_artifacts(db: Session) -> ChampionArtifacts:
    """
    Loads the model/calibrator/policy that the registry currently
    recognizes as champion -- the exact files referenced by the active
    ModelVersion / ModelDecisionPolicy rows. Never falls back to
    "latest on disk": a newer artifact set only becomes live once
    model_registry.promote_candidate_from_disk() has evaluated and
    approved it. Raises ChampionArtifactsUnavailable if no champion has
    ever been promoted, or its files are no longer on disk.
    """
    from app.services.mlops.model_registry import get_active_policy, get_champion_model_version

    champion_version = get_champion_model_version(db, algorithm="CatBoost")
    if champion_version is None:
        raise ChampionArtifactsUnavailable(
            "No champion model has been promoted yet. Run scripts/promote_champion.py "
            "after generating artifacts/ to promote a model before live risk scoring can run."
        )

    policy_row = get_active_policy(db, champion_version.id)
    if policy_row is None:
        raise ChampionArtifactsUnavailable(
            f"Champion ModelVersion {champion_version.id} has no active ModelDecisionPolicy."
        )

    model_path = champion_version.artifact_uri
    calibrator_path = champion_version.calibrator_uri
    if not (model_path and calibrator_path):
        raise ChampionArtifactsUnavailable(
            f"Champion ModelVersion {champion_version.id} has no artifact_uri/calibrator_uri recorded."
        )

    if not (os.path.isfile(model_path) and os.path.isfile(calibrator_path)):
        # Not on this machine's local disk -- fall back to the object-storage
        # location promote_candidate_from_disk() best-effort uploads to at
        # promotion time, so a model promoted elsewhere (e.g. the training
        # machine) can still be loaded here (e.g. a production machine).
        try:
            model_path = _download_cached(_object_storage_key(champion_version.id, "model.cbm"))
            calibrator_path = _download_cached(_object_storage_key(champion_version.id, "calibrator.pkl"))
        except Exception as exc:
            raise ChampionArtifactsUnavailable(
                f"Champion ModelVersion {champion_version.id} references artifacts not present on "
                f"local disk (model={champion_version.artifact_uri}, calibrator="
                f"{champion_version.calibrator_uri}), and fetching them from object storage also "
                f"failed: {exc}"
            ) from exc

    policy = {
        "t_accept": policy_row.accept_threshold,
        "t_contest": policy_row.contest_threshold,
        "c_fp": policy_row.fp_cost,
        "c_fn": policy_row.fn_cost,
        "c_review": policy_row.review_cost,
        "_policy_dir": policy_row.policy_version or "",
    }

    artifacts = _load_artifacts_from_paths(model_path, calibrator_path, policy)
    artifacts.model_version_id = champion_version.id
    artifacts.decision_policy_id = policy_row.id
    return artifacts
