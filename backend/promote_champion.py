"""
Operator entry point: evaluates the latest on-disk artifacts/ (model +
calibrator + decision policy, from run_benchmark.py -> ... ->
optimize_thresholds_step14.py, ideally followed by
evaluate_holdout_step15.py) against the current live champion's
recorded holdout performance, and promotes it to champion ONLY if it
is at least as good under the same cost configuration -- or there is
no existing champion yet.

This is the guardrail that was missing from the live risk-scoring
pipeline: before it, any model file dropped under artifacts/ became
"champion" the instant live scoring next ran, with no check of whether
it was actually better. Live scoring (app/services/ml/inference.py)
now reads only the registry's current active champion, never
"whatever's newest on disk" -- so promotion only ever happens here,
explicitly.

Usage:
    python promote_champion.py
"""

import sys

from app.main import app  # noqa: F401 -- imports every model module so SQLAlchemy can configure relationships
from app.core.database import SessionLocal
from app.services.mlops.champion_loader import ChampionArtifactsUnavailable
from app.services.mlops.model_registry import promote_candidate_from_disk


def main() -> int:
    db = SessionLocal()
    try:
        decision = promote_candidate_from_disk(db)
    except ChampionArtifactsUnavailable as exc:
        print(f"No candidate artifacts found: {exc}")
        return 1
    finally:
        db.close()

    print("PROMOTED" if decision.promoted else "REJECTED")
    print(f"Reason: {decision.reason}")
    print(f"ModelVersion: {decision.model_version.id} (status={decision.model_version.status})")
    print(f"  artifact_uri: {decision.model_version.artifact_uri}")
    if decision.decision_policy is not None:
        print(
            f"Active policy: t_accept={decision.decision_policy.accept_threshold} "
            f"t_contest={decision.decision_policy.contest_threshold} "
            f"(c_fp={decision.decision_policy.fp_cost} c_fn={decision.decision_policy.fn_cost} "
            f"c_review={decision.decision_policy.review_cost})"
        )
    return 0 if decision.promoted else 1


if __name__ == "__main__":
    sys.exit(main())
