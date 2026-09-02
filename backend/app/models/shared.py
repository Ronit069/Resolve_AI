import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, BigInteger, Integer, DateTime, ForeignKey, Enum, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base
from sqlalchemy.dialects.postgresql import UUID

class ProcessingState(str, enum.Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    INGESTED = "INGESTED"
    ENRICHING = "ENRICHING"
    ENRICHED = "ENRICHED"
    AWAITING_EVIDENCE = "AWAITING_EVIDENCE"
    EVIDENCE_READY = "EVIDENCE_READY"
    D_INTELLIGENCE_READY = "D_INTELLIGENCE_READY"
    E_VALIDATING = "E_VALIDATING"
    EVIDENCE_VALIDATED = "EVIDENCE_VALIDATED"
    FEATURE_READY = "FEATURE_READY"

class Merchant(Base):
    __tablename__ = "merchants"
    merchant_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_merchant_id = Column(String(100), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    cases = relationship("Case", back_populates="merchant")
    users = relationship("AppUser", back_populates="merchant")

class AppUserRole(str, enum.Enum):
    MERCHANT_ADMIN = "MERCHANT_ADMIN"
    RISK_ANALYST = "RISK_ANALYST"
    APPROVER = "APPROVER"
    SYSTEM_WORKER = "SYSTEM_WORKER"
    MODEL_MAINTAINER = "MODEL_MAINTAINER"

class AppUser(Base):
    __tablename__ = "app_users"
    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(UUID(as_uuid=True), ForeignKey("merchants.merchant_id"), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    role = Column(Enum(AppUserRole), nullable=False, server_default="APPROVER", default=AppUserRole.APPROVER)
    
    merchant = relationship("Merchant", back_populates="users")

class Case(Base):
    __tablename__ = "cases"
    case_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(UUID(as_uuid=True), ForeignKey("merchants.merchant_id"), nullable=False)
    external_dispute_id = Column(String(100), index=True, nullable=False)
    source = Column(String(30), nullable=False)  # razorpay, synthetic
    processing_state = Column(Enum(ProcessingState), default=ProcessingState.RECEIVED, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    merchant = relationship("Merchant", back_populates="cases")
    audit_logs = relationship("AuditLog", back_populates="case")

class AuditLog(Base):
    __tablename__ = "audit_logs"
    audit_id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True, autoincrement=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("app_users.user_id"), nullable=True)
    action = Column(String(100), nullable=False)
    details = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    case = relationship("Case", back_populates="audit_logs")

class ProcessingError(Base):
    __tablename__ = "processing_errors"
    error_id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True, autoincrement=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False)
    module = Column(String(50), nullable=False)
    error_code = Column(String(100), nullable=False)
    error_message = Column(String, nullable=True)
    retryable = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
