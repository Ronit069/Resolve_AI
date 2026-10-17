"""
Module L — training/live feature-distribution drift monitoring (issue #11).

Compares the feature distribution of recently-scored cases' live
CaseFeatureSnapshot.features_json against the champion's own training
distribution (synthetic_benchmark_v1_train.jsonl, written locally by
run_benchmark.py -> split_benchmark.py), using the Population Stability
Index (PSI) per numeric feature from app.services.ml.training.preprocessor's
own numerical_features list -- the same feature set the model is actually
trained on, not a hand-picked subset.

Same "repo-root script writes a local file, app/ reads it" convention
evaluation_artifact.py already uses for the Step 15 holdout artifact, and
the same honesty rule: no reference distribution available means a
DriftReferenceUnavailable callers must surface (503), never a fabricated
report. Distributing this reference to a serving machine that didn't run
the training pipeline itself is the same local-disk-only gap #6 solved
for the model/calibrator -- out of scope here; see that issue's pattern
(champion_loader's object-storage fallback) if this needs to travel.
"""

import json
import os
from typing import Any, Dict, List, Optional

import numpy as np
from sqlalchemy.orm import Session

from app.models.module_e import CaseFeatureSnapshot
from app.models.module_f import RiskPrediction
from app.services.ml.training.preprocessor import Preprocessor

TRAIN_FILE = "synthetic_benchmark_v1_train.jsonl"

# Standard PSI severity bands.
PSI_MODERATE_THRESHOLD = 0.1
PSI_SIGNIFICANT_THRESHOLD = 0.2

# A feature needs at least this many non-null observations on each side
# (reference and live) before a PSI comparison is considered meaningful.
MIN_OBSERVATIONS = 5


class DriftReferenceUnavailable(Exception):
    """Raised when the training reference distribution isn't on local disk."""


def _numeric_features() -> List[str]:
    return list(Preprocessor().numerical_features)


def load_reference_distribution(path: str = TRAIN_FILE) -> Dict[str, List[float]]:
    """
    Returns {feature_name: [non-null training values]} for every feature
    in Preprocessor().numerical_features. Raises DriftReferenceUnavailable
    if the training split isn't on disk (run the offline pipeline first).
    """
    if not os.path.exists(path):
        raise DriftReferenceUnavailable(
            f"No training reference distribution found at {path!r} -- run "
            "run_benchmark.py and split_benchmark.py first."
        )

    features = _numeric_features()
    values: Dict[str, List[float]] = {f: [] for f in features}

    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            ex = json.loads(line)
            row = ex.get("features", {})
            for feat in features:
                val = row.get(feat)
                if val is not None:
                    values[feat].append(float(val))

    return values


def get_live_feature_rows(db: Session, limit: int = 500) -> List[Dict[str, Any]]:
    """
    Returns up to `limit` feature dicts from the most recently scored
    cases (CaseFeatureSnapshot joined to its RiskPrediction), most recent
    first -- i.e. live traffic that actually went through score_case(),
    not every snapshot ever generated.
    """
    rows = (
        db.query(CaseFeatureSnapshot.features_json)
        .join(RiskPrediction, RiskPrediction.feature_snapshot_id == CaseFeatureSnapshot.id)
        .order_by(RiskPrediction.created_at.desc())
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows]


def compute_psi(reference_values: List[float], current_values: List[float], buckets: int = 10) -> Optional[float]:
    """
    Population Stability Index between a reference and current sample,
    bucketed by reference quantiles. Returns None when either sample is
    too small/degenerate to bucket meaningfully (never a fabricated 0.0).
    """
    if len(reference_values) < MIN_OBSERVATIONS or len(current_values) < MIN_OBSERVATIONS:
        return None

    ref = np.array(reference_values, dtype=float)
    cur = np.array(current_values, dtype=float)

    edges = np.unique(np.quantile(ref, np.linspace(0.0, 1.0, buckets + 1)))
    if len(edges) < 3:
        # Reference has too little spread (e.g. near-constant feature) to
        # bucket meaningfully -- PSI would be undefined, not zero.
        return None

    edges[0] = -np.inf
    edges[-1] = np.inf

    ref_counts, _ = np.histogram(ref, bins=edges)
    cur_counts, _ = np.histogram(cur, bins=edges)

    ref_pct = ref_counts / ref_counts.sum()
    cur_pct = cur_counts / cur_counts.sum()

    # Avoid log(0)/div-by-0 for empty buckets without distorting non-empty ones.
    eps = 1e-6
    ref_pct = np.where(ref_pct == 0, eps, ref_pct)
    cur_pct = np.where(cur_pct == 0, eps, cur_pct)

    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


def _severity(psi: Optional[float]) -> str:
    if psi is None:
        return "INSUFFICIENT_DATA"
    if psi >= PSI_SIGNIFICANT_THRESHOLD:
        return "SIGNIFICANT_DRIFT"
    if psi >= PSI_MODERATE_THRESHOLD:
        return "MODERATE_DRIFT"
    return "STABLE"


def compute_drift_report(
    reference_by_feature: Dict[str, List[float]], current_rows: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Pure function: given a reference distribution and a list of live
    feature dicts, returns a per-feature PSI drift report plus an overall
    summary. Never touches the DB or disk -- callers assemble its inputs.
    """
    features_report: Dict[str, Any] = {}
    max_psi = None
    drifted_features: List[str] = []

    for feat, reference_values in reference_by_feature.items():
        current_values = [float(r[feat]) for r in current_rows if r.get(feat) is not None]
        psi = compute_psi(reference_values, current_values)
        severity = _severity(psi)

        features_report[feat] = {
            "psi": psi,
            "severity": severity,
            "reference_count": len(reference_values),
            "reference_mean": float(np.mean(reference_values)) if reference_values else None,
            "current_count": len(current_values),
            "current_mean": float(np.mean(current_values)) if current_values else None,
        }

        if psi is not None:
            if max_psi is None or psi > max_psi:
                max_psi = psi
            if severity != "STABLE":
                drifted_features.append(feat)

    return {
        "live_sample_count": len(current_rows),
        "max_psi": max_psi,
        "overall_severity": _severity(max_psi) if current_rows else "INSUFFICIENT_DATA",
        "drifted_features": sorted(drifted_features),
        "features": features_report,
    }


def check_drift(db: Session, limit: int = 500) -> Dict[str, Any]:
    """
    Orchestrates the full drift check: loads the training reference
    distribution from local disk (raises DriftReferenceUnavailable if
    missing -- callers must surface that, not substitute a fabricated
    report) and compares it against the most recently scored cases'
    live feature snapshots.
    """
    reference_by_feature = load_reference_distribution()
    current_rows = get_live_feature_rows(db, limit=limit)
    return compute_drift_report(reference_by_feature, current_rows)
