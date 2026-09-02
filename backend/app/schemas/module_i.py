from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from uuid import UUID


class I03QueueItemSchema(BaseModel):
    case_id: UUID
    queue_item_id: UUID
    queue_status: str
    priority_score: float
    respond_by: datetime
    dispute_amount_minor: int
    dispute_currency: str
    dispute_reason_code: str
    dispute_status: str
    recommendation: Optional[str] = None
    hard_block: Optional[bool] = None


class I03QueueListingResponse(BaseModel):
    items: List[I03QueueItemSchema]
    total_count: int
    limit: int
    offset: int


class I07ActivityEventSchema(BaseModel):
    event_type: str  # "AUDIT_LOG" | "REVIEW_ACTION"
    event_id: str
    case_id: UUID
    actor_user_id: Optional[UUID] = None
    action: str
    details: Optional[str] = None
    created_at: datetime


class I07AuditFeedResponse(BaseModel):
    items: List[I07ActivityEventSchema]
    total_count: int
    limit: int
    offset: int
