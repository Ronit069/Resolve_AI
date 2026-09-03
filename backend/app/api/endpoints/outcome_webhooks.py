"""
Module K — outcome feedback endpoints.

Two routes live here (only one new endpoints file was authorized for
this module):
  - POST /webhooks/razorpay-outcome  — dedicated outcome-webhook ingestion,
    deliberately separate from Module A's /webhooks/razorpay endpoint
    (app/api/endpoints/webhooks.py), which is frozen and untouched.
  - POST /outcomes/{outcome_id}/curate — the explicit, MODEL_MAINTAINER-only
    curation action.
"""

import hashlib
import hmac
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.core.config import settings
from app.models.shared import AppUser, AppUserRole
from app.schemas.module_k import (
    CurationRequest,
    CurationResponse,
    OutcomeWebhookResponse,
    RazorpayOutcomeWebhookEvent,
)
from app.services.outcome_feedback.curation import CurationOutcomeNotFound, curate_outcome
from app.services.outcome_feedback.outcome_ingestion import OutcomeCaseUnresolvable, ingest_outcome_event

router = APIRouter()


@router.post("/webhooks/razorpay-outcome", response_model=OutcomeWebhookResponse, status_code=202)
async def razorpay_outcome_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives real dispute-outcome events (won/lost/closed) from Razorpay.
    Verifies HMAC signature using RAZORPAY_WEBHOOK_SECRET against the raw
    body, exactly as Module A's /webhooks/razorpay endpoint does — but this
    is a separate route/file; Module A's endpoint is not touched.
    """
    raw_body = await request.body()

    signature = request.headers.get("X-Razorpay-Signature")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")

    expected_signature = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Frozen contract: source_event_id MUST come from this header, never
    # from the JSON body.
    event_id = request.headers.get("X-Razorpay-Event-Id")
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing X-Razorpay-Event-Id header")

    try:
        json_payload = await request.json()
        payload = RazorpayOutcomeWebhookEvent(**json_payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload structure")

    entity = payload.payload.dispute.get("entity")
    if entity is None:
        raise HTTPException(status_code=400, detail="Missing payload.dispute.entity")

    occurred_at = datetime.fromtimestamp(payload.created_at, timezone.utc)

    try:
        outcome = ingest_outcome_event(
            db=db,
            event_type=payload.event,
            razorpay_dispute_id=entity.id,
            amount_deducted_minor=entity.amount_deducted,
            source_event_id=event_id,
            occurred_at=occurred_at,
        )
    except OutcomeCaseUnresolvable as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if outcome is None:
        return {"status": "ignored", "message": "Event type not handled, or a recognized duplicate event", "outcome_id": None}

    return {"status": "success", "message": "Outcome recorded", "outcome_id": str(outcome.id)}


@router.post("/outcomes/{outcome_id}/curate", response_model=CurationResponse, status_code=201)
def curate_outcome_endpoint(
    outcome_id: UUID,
    body: CurationRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(require_role([AppUserRole.MODEL_MAINTAINER])),
):
    """
    The only path in the codebase that may set
    CuratedFeedbackLabel.approved_for_training=True.
    """
    try:
        label = curate_outcome(
            db=db,
            outcome_id=outcome_id,
            label_name=body.label_name,
            label_value=body.label_value,
            label_quality=body.label_quality,
            curated_by=current_user.user_id,
            approved_for_training=body.approved_for_training,
        )
    except CurationOutcomeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "id": str(label.id),
        "outcome_id": str(label.outcome_id),
        "version": label.version,
        "approved_for_training": label.approved_for_training,
    }
