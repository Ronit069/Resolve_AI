from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from uuid import UUID

class QualityFlag(BaseModel):
    code: str
    message: str

class QualityAssessmentSchema(BaseModel):
    quality_score: Optional[float] = None
    quality_grade: Optional[str] = None
    flags: List[QualityFlag] = Field(default_factory=list)

class ExtractedFieldSchema(BaseModel):
    field_name: str
    value_type: str
    canonical_value: Optional[str] = None
    normalized_hash: Optional[str] = None
    field_confidence: float
    page_number: Optional[int] = None
    source_bbox: Optional[Dict[str, Any]] = None
    source_text_hash: Optional[str] = None
    review_status: str

class ArtifactReferencesSchema(BaseModel):
    normalized_text_object_key: Optional[str] = None
    layout_object_key: Optional[str] = None

class DocumentIntelligenceResult(BaseModel):
    case_id: UUID
    document_id: UUID
    evidence_type: str
    detected_document_type: Optional[str] = None
    type_match_status: str
    extraction_status: str
    schema_version: str
    overall_confidence: Optional[float] = None
    quality: QualityAssessmentSchema
    fields: List[ExtractedFieldSchema] = Field(default_factory=list)
    artifacts: ArtifactReferencesSchema
    pipeline_version: str
    ready_for_validation: bool

# Internal DTOs

class OCRBlockResult(BaseModel):
    text: str
    bbox: Dict[str, float]
    confidence: float

class PreprocessingMetadata(BaseModel):
    rotation_applied: float = 0.0
    deskewed: bool = False
    contrast_normalized: bool = False

class PageProcessingResult(BaseModel):
    page_number: int
    width_px: int
    height_px: int
    ocr_blocks: List[OCRBlockResult]
    preprocessing: PreprocessingMetadata
    ocr_confidence: float
