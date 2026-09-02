from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import datetime
from uuid import UUID
from app.models.module_c import ScanStatus, EvidenceProcessingStatus, EvidenceType, RequirementLevel

class EvidenceDocumentBase(BaseModel):
    evidence_type: EvidenceType

class EvidenceDocumentResponse(BaseModel):
    document_id: UUID
    case_id: UUID
    evidence_type: EvidenceType
    original_filename: Optional[str]
    mime_type: str
    file_size_bytes: int
    scan_status: ScanStatus
    processing_status: EvidenceProcessingStatus
    uploaded_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class EvidenceRequirementSchema(BaseModel):
    evidence_type: EvidenceType
    requirement_level: RequirementLevel

class EvidenceSummarySchema(BaseModel):
    required_count: int
    available_required_count: int
    missing_required: List[EvidenceType]
    coverage_ratio: float

class CaseEvidenceResponse(BaseModel):
    case_id: UUID
    evidence: List[EvidenceDocumentResponse]
    evidence_summary: EvidenceSummarySchema
    processing_state: str
    
    model_config = ConfigDict(from_attributes=True)
