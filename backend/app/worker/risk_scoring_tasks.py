"""
Module F — live risk scoring Celery task.

Dispatched by execute_evidence_validation (app/worker/validation_tasks.py)
immediately after it commits a case to FEATURE_READY. This is the
previously-missing link between feature generation and the human review
queue: scores the case's just-created CaseFeatureSnapshot
(app/services/ml/inference.py), persists a RiskPrediction, then hydrates
the review queue from it (app/services/review/queue.py) -- a function
that existed, fully tested, with zero production callers before this.
"""

import logging
import uuid

from app.core.database import SessionLocal
from app.models.shared import Case, ProcessingState
from app.services.ml.inference import score_case
from app.services.review.queue import hydrate_review_queue
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def score_risk_task(self, case_id: str):
    db = SessionLocal()
    try:
        case_uuid = uuid.UUID(case_id)

        prediction = score_case(db, case_uuid)
        if prediction is None:
            # No current feature snapshot yet, or no champion artifacts
            # available on this machine -- a clean no-op, not an error.
            # The case stays at FEATURE_READY; see score_case()'s own
            # docstring for exactly when this happens.
            db.commit()
            logger.info(
                f"score_risk_task: no-op for case_id={case_id} "
                "(no current feature snapshot, or no champion artifacts on this machine)"
            )
            return {"status": "SKIPPED", "case_id": case_id}

        hydrate_review_queue(db, case_uuid)

        case = db.query(Case).filter(Case.case_id == case_uuid).first()
        if case and case.processing_state == ProcessingState.FEATURE_READY:
            case.processing_state = ProcessingState.RISK_SCORED

        db.commit()
        logger.info(
            f"score_risk_task completed for case_id={case_id}: recommendation={prediction.recommendation}"
        )
        return {"status": "SCORED", "case_id": case_id, "recommendation": prediction.recommendation}

    except Exception as exc:
        db.rollback()
        logger.error(f"score_risk_task failed for case_id={case_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        db.close()
