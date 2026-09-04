from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime

class RazorpayDisputeEntity(BaseModel):
    id: str
    payment_id: str
    amount: int
    currency: str
    reason_code: str
    reason_description: str
    phase: str
    status: str
    created_at: int
    respond_by: Optional[int] = None
    
class RazorpayWebhookPayloadContains(BaseModel):
    dispute: Dict[str, RazorpayDisputeEntity]

class RazorpayWebhookEvent(BaseModel):
    entity: str
    account_id: str
    event: str
    contains: List[str]
    payload: RazorpayWebhookPayloadContains
    created_at: int

class SyntheticDisputePayload(BaseModel):
    external_event_id: str
    event_type: str
    account_id: str
    external_dispute_id: str
    payment_id: str
    amount_minor: int
    currency: str
    reason_code: str
    status: str
    phase: str
    dispute_created_at: datetime
    respond_by: Optional[datetime] = None
    event_time: datetime

class WebhookResponse(BaseModel):
    status: str
    message: str
    case_id: Optional[str] = None
