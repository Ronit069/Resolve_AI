import hmac
import hashlib
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import get_db
from app.schemas.module_a import RazorpayWebhookEvent, WebhookResponse
from app.services.ingestion import process_dispute_event

router = APIRouter()

@router.post("/razorpay", response_model=WebhookResponse, status_code=202)
async def razorpay_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Receive real dispute events from Razorpay.
    Verifies HMAC signature using RAZORPAY_WEBHOOK_SECRET.
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")
        
    # HMAC verification
    expected_signature = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        json_payload = await request.json()
        payload = RazorpayWebhookEvent(**json_payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON payload structure")

    event_id = request.headers.get("X-Razorpay-Event-Id", payload.entity + "_" + str(payload.created_at))
    event_time = datetime.fromtimestamp(payload.created_at, timezone.utc)
    
    # Extract inner dispute object
    dispute_data = payload.payload.dispute["entity"].model_dump()
    
    case_id = process_dispute_event(
        db=db,
        source="razorpay",
        raw_payload=raw_body,
        event_id=event_id,
        event_type=payload.event,
        event_time=event_time,
        dispute_data=dispute_data,
        account_id=payload.account_id
    )
    
    return {"status": "success", "message": "Webhook processed", "case_id": case_id}
