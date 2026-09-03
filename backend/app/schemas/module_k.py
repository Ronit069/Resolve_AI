from pydantic import BaseModel
from typing import Optional, Dict

from app.models.module_h import LabelQuality


class OutcomeDisputeEntity(BaseModel):
    id: str
    status: str
    amount_deducted: Optional[int] = None


class OutcomeWebhookPayloadContains(BaseModel):
    dispute: Dict[str, OutcomeDisputeEntity]


class RazorpayOutcomeWebhookEvent(BaseModel):
    event: str
    payload: OutcomeWebhookPayloadContains
    created_at: int


class OutcomeWebhookResponse(BaseModel):
    status: str
    message: str
    outcome_id: Optional[str] = None


class CurationRequest(BaseModel):
    label_name: str
    label_value: str
    label_quality: LabelQuality
    approved_for_training: bool = False


class CurationResponse(BaseModel):
    id: str
    outcome_id: str
    version: int
    approved_for_training: bool
