from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
import json

from app.core.config import settings
from app.core.database import get_db
from app.schemas.module_a import SyntheticDisputePayload, WebhookResponse
from app.services.ingestion import process_dispute_event

router = APIRouter()

@router.post("/disputes", response_model=WebhookResponse, status_code=202)
async def dev_synthetic_dispute(
    payload: SyntheticDisputePayload,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Inject synthetic dispute through same canonical path.
    Only available if ENABLE_DEV_ENDPOINTS is True.
    """
    if not settings.ENABLE_DEV_ENDPOINTS:
        raise HTTPException(status_code=403, detail="Development endpoints are disabled in this environment")
        
    raw_body = await request.body()
    
    case_id = process_dispute_event(
        db=db,
        source="synthetic",
        raw_payload=raw_body,
        event_id=payload.external_event_id,
        event_type=payload.event_type,
        event_time=payload.event_time,
        dispute_data=payload.model_dump(),
        account_id=payload.account_id
    )
    
    return {"status": "success", "message": "Synthetic webhook processed", "case_id": case_id}
