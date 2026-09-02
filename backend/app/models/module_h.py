import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, BigInteger, DateTime, ForeignKey, Boolean, Numeric, Text, UniqueConstraint, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base
from app.models.module_d import JSONVariant

class QueueStatus(str, enum.Enum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    DONE = "DONE"

class ReviewActionEnum(str, enum.Enum):
    APPROVE_CONTEST = "APPROVE_CONTEST"
    APPROVE_ACCEPT = "APPROVE_ACCEPT"
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
    EDIT_DRAFT = "EDIT_DRAFT"
    REJECT_RECOMMENDATION = "REJECT_RECOMMENDATION"
    ESCALATE = "ESCALATE"
    ACCEPT = "ACCEPT"
    REQUEST_MORE = "REQUEST_MORE"

class ContestPackageStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"

class ExternalActionType(str, enum.Enum):
    UPLOAD_DOCUMENT = "UPLOAD_DOCUMENT"
    CONTEST_DRAFT = "CONTEST_DRAFT"
    CONTEST_SUBMIT = "CONTEST_SUBMIT"
    ACCEPT = "ACCEPT"

class OutboxStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    FAILED = "FAILED"

class SubmissionStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PENDING = "PENDING"

class DisputeOutcomeEnum(str, enum.Enum):
    WON = "WON"
    LOST = "LOST"
    CLOSED = "CLOSED"
    UNDER_REVIEW = "UNDER_REVIEW"

class LabelQuality(str, enum.Enum):
    GOLD = "GOLD"
    SILVER = "SILVER"
    SYNTHETIC = "SYNTHETIC"


class ReviewQueueItem(Base):
    """Table H1 - review_queue_items"""
    __tablename__ = "review_queue_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False, index=True)
    prediction_id = Column(UUID(as_uuid=True), ForeignKey("risk_predictions.prediction_id"), nullable=False, index=True)
    draft_id = Column(UUID(as_uuid=True), ForeignKey("generated_drafts.id"), nullable=True, index=True)
    priority_score = Column(Numeric, nullable=False)
    queue_status = Column(Enum(QueueStatus), nullable=False, default=QueueStatus.PENDING)
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("app_users.user_id"), nullable=True, index=True)
    respond_by = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class ReviewAction(Base):
    """Table H2 - review_actions"""
    __tablename__ = "review_actions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id = Column(UUID(as_uuid=True), ForeignKey("review_queue_items.id"), nullable=False, index=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False, index=True)
    reviewer_id = Column(UUID(as_uuid=True), ForeignKey("app_users.user_id"), nullable=False, index=True)
    action = Column(Enum(ReviewActionEnum), nullable=False)
    override_reason_code = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    draft_revision_json = Column(JSONVariant, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class ContestPackage(Base):
    """Table H3 - contest_packages"""
    __tablename__ = "contest_packages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False, index=True)
    review_action_id = Column(UUID(as_uuid=True), ForeignKey("review_actions.id"), nullable=False, index=True)
    draft_id = Column(UUID(as_uuid=True), ForeignKey("generated_drafts.id"), nullable=False, index=True)
    contest_amount_minor = Column(BigInteger, nullable=False)
    summary = Column(Text, nullable=False)
    package_hash = Column(String(255), nullable=False)
    status = Column(Enum(ContestPackageStatus), nullable=False, default=ContestPackageStatus.DRAFT)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class ContestPackageDocument(Base):
    """Table H4 - contest_package_documents"""
    __tablename__ = "contest_package_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contest_package_id = Column(UUID(as_uuid=True), ForeignKey("contest_packages.id"), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=True), ForeignKey("evidence_documents.document_id"), nullable=False, index=True)
    razorpay_evidence_field = Column(String(255), nullable=False)
    approved = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)


class RazorpayDocumentLink(Base):
    """Table H5 - razorpay_document_links"""
    __tablename__ = "razorpay_document_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("evidence_documents.document_id"), nullable=False, index=True)
    razorpay_document_id = Column(String(255), unique=True, nullable=False)
    purpose = Column(String(255), nullable=False)
    mime_type = Column(String(255), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    external_response_json = Column(JSONVariant, nullable=False)


class ExternalActionOutbox(Base):
    """Table H6 - external_action_outbox"""
    __tablename__ = "external_action_outbox"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False, index=True)
    action_type = Column(Enum(ExternalActionType), nullable=False)
    aggregate_id = Column(UUID(as_uuid=True), nullable=False)
    payload_json = Column(JSONVariant, nullable=False)
    idempotency_key = Column(String(255), unique=True, nullable=False)
    status = Column(Enum(OutboxStatus), nullable=False, default=OutboxStatus.PENDING)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class ExternalActionAttempt(Base):
    """Table H7 - external_action_attempts"""
    __tablename__ = "external_action_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    outbox_id = Column(UUID(as_uuid=True), ForeignKey("external_action_outbox.id"), nullable=False, index=True)
    attempt_no = Column(Integer, nullable=False)
    request_metadata = Column(JSONVariant, nullable=False)
    http_status = Column(Integer, nullable=True)
    response_metadata = Column(JSONVariant, nullable=True)
    error_code = Column(String(255), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)


class ContestSubmission(Base):
    """Table H8 - contest_submissions"""
    __tablename__ = "contest_submissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contest_package_id = Column(UUID(as_uuid=True), ForeignKey("contest_packages.id"), nullable=False, index=True)
    external_dispute_id = Column(String(255), nullable=False)
    action = Column(String(255), nullable=False)
    external_status = Column(String(255), nullable=False)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    razorpay_evidence_json = Column(JSONVariant, nullable=False)
    response_snapshot = Column(JSONVariant, nullable=False)
    status = Column(Enum(SubmissionStatus), nullable=False, default=SubmissionStatus.PENDING)


class DisputeOutcome(Base):
    """Table H9 - dispute_outcomes"""
    __tablename__ = "dispute_outcomes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False, index=True)
    prediction_id = Column(UUID(as_uuid=True), ForeignKey("risk_predictions.prediction_id"), nullable=True, index=True)
    contest_submission_id = Column(UUID(as_uuid=True), ForeignKey("contest_submissions.id"), nullable=True, index=True)
    outcome = Column(Enum(DisputeOutcomeEnum), nullable=False)
    amount_deducted_minor = Column(BigInteger, nullable=True)
    source_event_id = Column(String(255), nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class CuratedFeedbackLabel(Base):
    """Table H10 - curated_feedback_labels"""
    __tablename__ = "curated_feedback_labels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False, index=True)
    outcome_id = Column(UUID(as_uuid=True), ForeignKey("dispute_outcomes.id"), nullable=True, index=True)
    label_name = Column(String(255), nullable=False)
    label_value = Column(String(255), nullable=False)
    label_quality = Column(Enum(LabelQuality), nullable=False)
    curated_by = Column(UUID(as_uuid=True), ForeignKey("app_users.user_id"), nullable=True, index=True)
    version = Column(Integer, nullable=False, default=1)
    approved_for_training = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
