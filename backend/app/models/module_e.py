import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, BigInteger, Integer, DateTime, ForeignKey, Enum, Numeric, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base
from app.models.module_d import JSONVariant

class EValidationRunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class ERuleSeverity(str, enum.Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"

class EValidationResultState(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    UNKNOWN = "UNKNOWN"
    NA = "NA"

class ERequirementState(str, enum.Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    UNUSABLE = "UNUSABLE"

class EMatchMethod(str, enum.Enum):
    EXACT = "EXACT"
    FUZZY = "FUZZY"
    TOLERANCE = "TOLERANCE"

class ELinkStatus(str, enum.Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"

class EFeatureDataType(str, enum.Enum):
    NUMERIC = "NUMERIC"
    CATEGORICAL = "CATEGORICAL"
    BOOLEAN = "BOOLEAN"


class EvidencePolicyVersion(Base):
    __tablename__ = "evidence_policy_versions"
    __table_args__ = (
        UniqueConstraint("payment_network", "reason_code", "phase", "version", name="uq_ev_pol_ver_identity"),
    )

    policy_version_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_network = Column(String(100), nullable=False)
    reason_code = Column(String(100), nullable=False)
    phase = Column(String(100), nullable=False)
    version = Column(Integer, nullable=False)
    effective_from = Column(DateTime(timezone=True), nullable=False)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class EvidencePolicyRuleVersion(Base):
    __tablename__ = "evidence_policy_rule_versions"
    __table_args__ = (
        UniqueConstraint("policy_version_id", "rule_id", name="uq_ev_pol_rule_identity"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_version_id = Column(UUID(as_uuid=True), ForeignKey("evidence_policy_versions.policy_version_id"), nullable=False)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("validation_rule_catalog.rule_id"), nullable=False)
    rule_version_id = Column(UUID(as_uuid=True), ForeignKey("validation_rule_versions.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    policy_version = relationship("EvidencePolicyVersion")
    rule = relationship("ValidationRuleCatalog")
    rule_version = relationship("ValidationRuleVersion")



class EvidenceValidationRun(Base):
    __tablename__ = "evidence_validation_runs"
    __table_args__ = (
        Index("idx_ev_val_run_case_created", "case_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    evidence_version = Column(String(100), nullable=False)
    policy_version_id = Column(UUID(as_uuid=True), ForeignKey("evidence_policy_versions.policy_version_id"), nullable=False)
    status = Column(Enum(EValidationRunStatus), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(100), nullable=True)
    idempotency_key = Column(String(200), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    
    case = relationship("Case")
    policy_version = relationship("EvidencePolicyVersion")


class ValidationRuleCatalog(Base):
    __tablename__ = "validation_rule_catalog"

    rule_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_code = Column(String(100), unique=True, nullable=False)
    category = Column(String(100), nullable=False)
    description = Column(String, nullable=False)
    severity_default = Column(Enum(ERuleSeverity), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class ValidationRuleVersion(Base):
    __tablename__ = "validation_rule_versions"
    __table_args__ = (
        UniqueConstraint("rule_id", "version", name="uq_val_rule_version"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("validation_rule_catalog.rule_id"), nullable=False)
    version = Column(Integer, nullable=False)
    parameters_json = Column(JSONVariant, nullable=False, default=dict)
    effective_from = Column(DateTime(timezone=True), nullable=False)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    checksum = Column(String(64), nullable=False)
    
    rule = relationship("ValidationRuleCatalog")


class EvidenceValidationResult(Base):
    __tablename__ = "evidence_validation_results"
    __table_args__ = (
        Index("idx_ev_val_res_run_severity", "validation_run_id", "severity"),
        Index("idx_ev_val_res_run_result", "validation_run_id", "result"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    validation_run_id = Column(UUID(as_uuid=True), ForeignKey("evidence_validation_runs.id"), nullable=False)
    rule_version_id = Column(UUID(as_uuid=True), ForeignKey("validation_rule_versions.id"), nullable=False)
    result = Column(Enum(EValidationResultState), nullable=False)
    severity = Column(Enum(ERuleSeverity), nullable=False)
    source_refs = Column(JSONVariant, nullable=True)
    normalized_values = Column(JSONVariant, nullable=True)
    explanation = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    run = relationship("EvidenceValidationRun")
    rule_version = relationship("ValidationRuleVersion")


class EvidenceRequirementAssessment(Base):
    __tablename__ = "evidence_requirement_assessments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    validation_run_id = Column(UUID(as_uuid=True), ForeignKey("evidence_validation_runs.id"), index=True, nullable=False)
    evidence_type = Column(String(100), nullable=False)
    requirement_level = Column(String(50), nullable=False)  # Map to RequirementLevel enum values if needed
    status = Column(Enum(ERequirementState), nullable=False)
    document_ids = Column(JSONVariant, nullable=True)
    coverage_weight = Column(Numeric(5, 4), nullable=True)
    reason = Column(String, nullable=True)
    
    run = relationship("EvidenceValidationRun")


class CrossSourceFieldLink(Base):
    __tablename__ = "cross_source_field_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    validation_run_id = Column(UUID(as_uuid=True), ForeignKey("evidence_validation_runs.id"), index=True, nullable=False)
    semantic_field = Column(String(100), nullable=False)
    left_source = Column(JSONVariant, nullable=False)
    right_source = Column(JSONVariant, nullable=False)
    match_method = Column(Enum(EMatchMethod), nullable=False)
    match_score = Column(Numeric(5, 4), nullable=True)
    link_status = Column(Enum(ELinkStatus), nullable=False)

    run = relationship("EvidenceValidationRun")


class FeatureDefinition(Base):
    __tablename__ = "feature_definitions"

    feature_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feature_name = Column(String(100), unique=True, nullable=False)
    data_type = Column(Enum(EFeatureDataType), nullable=False)
    definition = Column(String, nullable=False)
    source_modules = Column(String(100), nullable=False)
    version = Column(Integer, nullable=False)
    available_at_prediction = Column(Boolean, nullable=False)
    active = Column(Boolean, nullable=False, default=True)


class CaseFeatureSnapshot(Base):
    __tablename__ = "case_feature_snapshots"
    __table_args__ = (
        UniqueConstraint("validation_run_id", name="uq_case_feature_snapshot_run"),
        Index("idx_cfs_case_current", "case_id", "is_current"),
        Index("idx_cfs_feature_hash", "feature_hash"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False)
    validation_run_id = Column(UUID(as_uuid=True), ForeignKey("evidence_validation_runs.id"), nullable=False)
    feature_schema_version = Column(String(50), nullable=False)
    features_json = Column(JSONVariant, nullable=False)
    feature_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    is_current = Column(Boolean, nullable=False, default=True)

    case = relationship("Case")
    run = relationship("EvidenceValidationRun")
