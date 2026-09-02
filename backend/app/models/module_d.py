import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, BigInteger, Integer, DateTime, ForeignKey, Enum, Numeric, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base
from sqlalchemy.types import TypeDecorator, JSON

# SQLite compatibility for JSONB
class JSONVariant(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(JSONB())
        else:
            return dialect.type_descriptor(JSON())

class DocumentProcessingJob(Base):
    __tablename__ = "document_processing_jobs"
    __table_args__ = (
        Index("idx_doc_jobs_document_status", "document_id", "status"),
        Index("idx_doc_jobs_case_status", "case_id", "status"),
    )

    job_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("evidence_documents.document_id"), index=True, nullable=False)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    merchant_id = Column(UUID(as_uuid=True), ForeignKey("merchants.merchant_id"), index=True, nullable=False)
    job_type = Column(String(40), nullable=False)
    status = Column(String(30), index=True, nullable=False)
    attempt_no = Column(Integer, nullable=False, default=1)
    idempotency_key = Column(String(160), unique=True, nullable=False)
    pipeline_version = Column(String(60), nullable=False)
    ocr_model_version_id = Column(UUID(as_uuid=True), ForeignKey("document_model_versions.model_version_id"), nullable=True)
    extractor_model_version_id = Column(UUID(as_uuid=True), ForeignKey("document_model_versions.model_version_id"), nullable=True)
    queued_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_code = Column(String(80), index=True, nullable=True)
    error_message_masked = Column(String(500), nullable=True)

    document = relationship("EvidenceDocument")
    case = relationship("Case")
    merchant = relationship("Merchant")
    pages = relationship("DocumentPage", back_populates="job", cascade="all, delete-orphan")
    extraction = relationship("DocumentExtraction", back_populates="job", uselist=False, cascade="all, delete-orphan")
    quality_assessment = relationship("DocumentQualityAssessment", back_populates="job", uselist=False, cascade="all, delete-orphan")
    ocr_model = relationship("DocumentModelVersion", foreign_keys=[ocr_model_version_id])
    extractor_model = relationship("DocumentModelVersion", foreign_keys=[extractor_model_version_id])


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("job_id", "page_number", name="uq_document_pages_job_page"),
    )

    page_id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("document_processing_jobs.job_id"), index=True, nullable=False)
    document_id = Column(UUID(as_uuid=True), ForeignKey("evidence_documents.document_id"), index=True, nullable=False)
    page_number = Column(Integer, nullable=False)
    native_text_used = Column(Boolean, nullable=False, default=False)
    page_artifact_key = Column(String(500), nullable=True)
    ocr_text_object_key = Column(String(500), nullable=True)
    layout_object_key = Column(String(500), nullable=True)
    width_px = Column(Integer, nullable=True)
    height_px = Column(Integer, nullable=True)
    rotation_degrees = Column(Numeric(6, 2), nullable=True)
    preprocessing_json = Column(JSONVariant, nullable=False, default=dict)
    ocr_confidence = Column(Numeric(5, 4), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    job = relationship("DocumentProcessingJob", back_populates="pages")
    document = relationship("EvidenceDocument")


class DocumentExtraction(Base):
    __tablename__ = "document_extractions"
    __table_args__ = (
        Index("idx_doc_ext_doc_created", "document_id", "created_at"),
    )

    extraction_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("document_processing_jobs.job_id"), unique=True, nullable=False)
    document_id = Column(UUID(as_uuid=True), ForeignKey("evidence_documents.document_id"), index=True, nullable=False)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    expected_evidence_type = Column(String(60), nullable=False)
    detected_document_type = Column(String(60), index=True, nullable=True)
    type_match_status = Column(String(30), nullable=False)
    extraction_status = Column(String(30), index=True, nullable=False)
    schema_version = Column(String(40), nullable=False)
    extracted_json = Column(JSONVariant, nullable=False, default=dict)
    overall_confidence = Column(Numeric(5, 4), nullable=True)
    requires_review = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    job = relationship("DocumentProcessingJob", back_populates="extraction")
    document = relationship("EvidenceDocument")
    case = relationship("Case")
    fields = relationship("ExtractedField", back_populates="extraction", cascade="all, delete-orphan")


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (
        Index("idx_ext_fields_ext_name", "extraction_id", "field_name"),
        Index("idx_ext_fields_doc_name", "document_id", "field_name"),
    )

    field_id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    extraction_id = Column(UUID(as_uuid=True), ForeignKey("document_extractions.extraction_id"), index=True, nullable=False)
    document_id = Column(UUID(as_uuid=True), ForeignKey("evidence_documents.document_id"), index=True, nullable=False)
    field_name = Column(String(100), index=True, nullable=False)
    value_type = Column(String(30), nullable=False)
    canonical_value_text = Column(String, nullable=True)
    numeric_value = Column(Numeric(18, 4), nullable=True)
    date_value = Column(DateTime, nullable=True)  # SQLite date compat
    timestamp_value = Column(DateTime(timezone=True), nullable=True)
    currency_code = Column(String(3), nullable=True)
    normalized_hash = Column(String(64), index=True, nullable=True)
    raw_value_masked = Column(String(500), nullable=True)
    field_confidence = Column(Numeric(5, 4), nullable=False)
    page_number = Column(Integer, nullable=True)
    source_bbox = Column(JSONVariant, nullable=True)
    source_text_hash = Column(String(64), nullable=True)
    review_status = Column(String(30), nullable=False, default="NOT_REQUIRED")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    extraction = relationship("DocumentExtraction", back_populates="fields")
    document = relationship("EvidenceDocument")


class DocumentQualityAssessment(Base):
    __tablename__ = "document_quality_assessments"

    quality_id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("document_processing_jobs.job_id"), unique=True, nullable=False)
    document_id = Column(UUID(as_uuid=True), ForeignKey("evidence_documents.document_id"), index=True, nullable=False)
    blur_score = Column(Numeric(8, 4), nullable=True)
    skew_degrees = Column(Numeric(6, 2), nullable=True)
    resolution_dpi = Column(Integer, nullable=True)
    readable_ratio = Column(Numeric(5, 4), nullable=True)
    ocr_coverage_ratio = Column(Numeric(5, 4), nullable=True)
    cropping_suspected = Column(Boolean, nullable=False, default=False)
    low_contrast = Column(Boolean, nullable=False, default=False)
    quality_score = Column(Numeric(5, 4), nullable=True)
    quality_grade = Column(String(20), nullable=True)
    quality_flags = Column(JSONVariant, nullable=False, default=list)
    assessed_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    job = relationship("DocumentProcessingJob", back_populates="quality_assessment")
    document = relationship("EvidenceDocument")


class DocumentModelVersion(Base):
    __tablename__ = "document_model_versions"
    __table_args__ = (
        Index("idx_doc_model_comp_act", "component", "active"),
    )

    model_version_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    component = Column(String(40), index=True, nullable=False)
    model_name = Column(String(120), nullable=False)
    version = Column(String(80), nullable=False)
    config_hash = Column(String(64), nullable=False)
    artifact_reference = Column(String(500), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class CaseDocumentIntelligenceStatus(Base):
    __tablename__ = "case_document_intelligence_status"
    __table_args__ = (
        Index("idx_case_doc_intel_ready_status", "ready_for_module_e", "overall_status"),
    )

    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), primary_key=True, nullable=False)
    total_safe_documents = Column(Integer, nullable=False, default=0)
    queued_documents = Column(Integer, nullable=False, default=0)
    processed_documents = Column(Integer, nullable=False, default=0)
    successful_documents = Column(Integer, nullable=False, default=0)
    review_required_documents = Column(Integer, nullable=False, default=0)
    failed_documents = Column(Integer, nullable=False, default=0)
    blocking_failures = Column(Integer, nullable=False, default=0)
    overall_status = Column(String(30), index=True, nullable=False)
    ready_for_module_e = Column(Boolean, index=True, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    case = relationship("Case")
