import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, BigInteger, Integer, DateTime, ForeignKey, Enum, JSON, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base
import enum

class ScanStatus(str, enum.Enum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    FAILED = "FAILED"

class EvidenceProcessingStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    QUARANTINED = "QUARANTINED"
    READY_FOR_OCR = "READY_FOR_OCR"
    REJECTED = "REJECTED"
    CORRUPTED = "CORRUPTED"
    SCAN_FAILED = "SCAN_FAILED"
    # Module D extensions
    OCR_QUEUED = "OCR_QUEUED"
    OCR_PROCESSING = "OCR_PROCESSING"
    EXTRACTED = "EXTRACTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    OCR_FAILED = "OCR_FAILED"
    REPROCESS_REQUESTED = "REPROCESS_REQUESTED"

class RequirementLevel(str, enum.Enum):
    REQUIRED = "REQUIRED"
    RECOMMENDED = "RECOMMENDED"
    OPTIONAL = "OPTIONAL"

class EvidenceType(str, enum.Enum):
    INVOICE = "INVOICE"
    PROOF_OF_DELIVERY = "PROOF_OF_DELIVERY"
    COURIER_TRACKING = "COURIER_TRACKING"
    REFUND_RECEIPT = "REFUND_RECEIPT"
    CUSTOMER_COMMUNICATION = "CUSTOMER_COMMUNICATION"
    SERVICE_CONFIRMATION = "SERVICE_CONFIRMATION"
    TERMS_ACCEPTANCE = "TERMS_ACCEPTANCE"
    OTHER = "OTHER"

class EvidenceDocument(Base):
    __tablename__ = "evidence_documents"
    __table_args__ = (
        UniqueConstraint("case_id", "sha256", name="uq_evidence_case_sha256"),
    )

    document_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    merchant_id = Column(UUID(as_uuid=True), ForeignKey("merchants.merchant_id"), index=True, nullable=False)
    evidence_type = Column(String(60), index=True, nullable=False)
    object_key = Column(String(500), unique=True, nullable=False)
    original_filename = Column(String(255), nullable=True)
    mime_type = Column(String(100), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False)
    sha256 = Column(String(64), index=True, nullable=False)
    scan_status = Column(Enum(ScanStatus), nullable=False, default=ScanStatus.PENDING)
    processing_status = Column(Enum(EvidenceProcessingStatus), index=True, nullable=False, default=EvidenceProcessingStatus.UPLOADED)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("app_users.user_id"), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    case = relationship("Case", backref="evidence_documents")
    scan_results = relationship("MalwareScanResult", back_populates="document", cascade="all, delete-orphan")


class MalwareScanResult(Base):
    __tablename__ = "malware_scan_results"

    scan_id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    document_id = Column(UUID(as_uuid=True), ForeignKey("evidence_documents.document_id"), index=True, nullable=False)
    scanner = Column(String(80), nullable=False)
    scanner_version = Column(String(80), nullable=True)
    scan_status = Column(Enum(ScanStatus), nullable=False)
    signature_name = Column(String(255), nullable=True)
    scanned_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    duration_ms = Column(Integer, nullable=True)

    document = relationship("EvidenceDocument", back_populates="scan_results")


class EvidenceRequirement(Base):
    __tablename__ = "evidence_requirements"

    requirement_id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    reason_code = Column(String(100), index=True, nullable=False)
    evidence_type = Column(String(60), nullable=False)
    requirement_level = Column(Enum(RequirementLevel), nullable=False, default=RequirementLevel.REQUIRED)
    policy_version = Column(String(40), nullable=False, default="1.0")
    active = Column(Boolean, nullable=False, default=True)


class CaseEvidenceStatus(Base):
    __tablename__ = "case_evidence_status"

    case_evidence_status_pk = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, unique=True, nullable=False)
    reason_code = Column(String(100), nullable=False)
    required_count = Column(Integer, nullable=False, default=0)
    available_required_count = Column(Integer, nullable=False, default=0)
    missing_required = Column(JSON, nullable=False, default=list) # SQLite compat JSON
    coverage_ratio = Column(Numeric(5, 4), nullable=False, default=0.0)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    case = relationship("Case", backref="evidence_status")


class EvidenceAccessEvent(Base):
    __tablename__ = "evidence_access_events"

    evidence_event_pk = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    document_id = Column(UUID(as_uuid=True), ForeignKey("evidence_documents.document_id"), index=True, nullable=False)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("app_users.user_id"), nullable=True)
    action = Column(String(30), nullable=False)
    result = Column(String(30), nullable=False)
    occurred_at = Column(DateTime(timezone=True), index=True, nullable=False, default=lambda: datetime.now(timezone.utc))
    request_id = Column(String(100), index=True, nullable=True)

    document = relationship("EvidenceDocument")
    case = relationship("Case")
