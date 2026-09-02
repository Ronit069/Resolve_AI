from sqlalchemy import Column, String, Boolean, BigInteger, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base

class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    webhook_event_pk = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    external_event_id = Column(String(120), unique=True, nullable=False)
    event_type = Column(String(80), index=True, nullable=False)
    source = Column(String(30), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    signature_verified = Column(Boolean, nullable=False)
    received_at = Column(DateTime(timezone=True), index=True, nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(30), nullable=False)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=True)
    
    case = relationship("Case")

class Dispute(Base):
    __tablename__ = "disputes"
    dispute_pk = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), unique=True, nullable=False)
    external_dispute_id = Column(String(120), index=True, nullable=False)
    payment_id = Column(String(120), index=True, nullable=False)
    amount_minor = Column(BigInteger, nullable=False)
    currency = Column(String(3), nullable=False)
    reason_code = Column(String(100), index=True, nullable=False)
    phase = Column(String(60), nullable=True)
    status = Column(String(60), index=True, nullable=False)
    dispute_created_at = Column(DateTime(timezone=True), nullable=False)
    respond_by = Column(DateTime(timezone=True), index=True, nullable=True)
    source_updated_at = Column(DateTime(timezone=True), nullable=True)
    
    case = relationship("Case", backref="dispute_details")

class DisputeEvent(Base):
    __tablename__ = "dispute_events"
    dispute_event_pk = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    external_event_id = Column(String(120), ForeignKey("webhook_events.external_event_id"), nullable=False)
    old_status = Column(String(60), nullable=True)
    new_status = Column(String(60), nullable=False)
    event_time = Column(DateTime(timezone=True), index=True, nullable=False)
    accepted_transition = Column(Boolean, nullable=False)
    reason = Column(String(255), nullable=True)
    
    case = relationship("Case")
    webhook_event = relationship("WebhookEvent")
