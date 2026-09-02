"""
Module B — Manual enrichment endpoint.

POST /api/v1/cases/{case_id}/enrich
  - Validates case exists.
  - Checks case state allows enrichment.
  - Prevents duplicate concurrent enrichment.
  - Dispatches enrichment asynchronously via Celery.
  - Returns 202 Accepted.
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
import uuid as uuid_mod

from app.core.database import get_db
from app.models.shared import Case, ProcessingState
from app.schemas.module_b import EnrichResponse
from app.worker.tasks import enrich_dispute_task

router = APIRouter()


@router.post("/{case_id}/enrich", response_model=EnrichResponse, status_code=202)
def trigger_enrichment(
    case_id: str,
    db: Session = Depends(get_db),
):
    """
    Manually trigger enrichment for a case (admin/dev/retry use).
    Dispatches the enrichment task asynchronously and returns immediately.
    """
    # Validate UUID format
    try:
        case_uuid = uuid_mod.UUID(case_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid case_id format")

    # Verify case exists
    case = db.query(Case).filter(Case.case_id == case_uuid).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    # State guard: prevent concurrent enrichment
    if case.processing_state == ProcessingState.ENRICHING:
        raise HTTPException(
            status_code=409,
            detail=f"Case {case_id} is already being enriched. "
                   f"Wait for current enrichment to complete or fail.",
        )

    # State guard: must be in a state that allows enrichment
    allowed_states = (
        ProcessingState.INGESTED,
        ProcessingState.ENRICHED,  # Re-enrichment allowed
    )
    if case.processing_state not in allowed_states:
        raise HTTPException(
            status_code=422,
            detail=f"Case {case_id} in state {case.processing_state.value}, "
                   f"cannot trigger enrichment. Must be INGESTED or ENRICHED.",
        )

    # Dispatch asynchronously
    try:
        enrich_dispute_task.delay(str(case.case_id))
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to dispatch enrichment task: {str(e)}",
        )

    return EnrichResponse(
        status="accepted",
        message="Enrichment task dispatched",
        case_id=str(case.case_id),
        current_state=case.processing_state.value,
    )
