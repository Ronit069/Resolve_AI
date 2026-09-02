from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
from app.models.shared import ProcessingState

class MerchantBase(BaseModel):
    external_merchant_id: str
    name: str

class MerchantResponse(MerchantBase):
    merchant_id: UUID
    is_active: bool
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class CaseBase(BaseModel):
    external_dispute_id: str
    source: str

class CaseResponse(CaseBase):
    case_id: UUID
    merchant_id: UUID
    processing_state: ProcessingState
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class ProcessingErrorCreate(BaseModel):
    case_id: UUID
    module: str
    error_code: str
    error_message: Optional[str] = None
    retryable: bool = False
