"""
Operator entry point: reverts to the most recently-retired CatBoost
champion, re-activating its decision policy. Use this when a promoted
champion turns out bad in a way the promote_champion.py guardrail
didn't catch (it only compares offline holdout metrics -- a live-only
regression needs a human to call this).

Usage:
    python rollback_champion.py
"""

import sys

from app.main import app  # noqa: F401 -- imports every model module so SQLAlchemy can configure relationships
from app.core.database import SessionLocal
from app.services.mlops.model_registry import rollback_to_previous_champion


def main() -> int:
    db = SessionLocal()
    try:
        champion, policy = rollback_to_previous_champion(db)
    except ValueError as exc:
        print(f"Rollback failed: {exc}")
        return 1
    finally:
        db.close()

    print(f"Rolled back to ModelVersion {champion.id} (status={champion.status})")
    print(f"  artifact_uri: {champion.artifact_uri}")
    print(
        f"Active policy: t_accept={policy.accept_threshold} t_contest={policy.contest_threshold} "
        f"(c_fp={policy.fp_cost} c_fn={policy.fn_cost} c_review={policy.review_cost})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
