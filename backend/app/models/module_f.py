import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Boolean, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base
from app.models.module_d import JSONVariant

class MLLabel(Base):
    __tablename__ = "ml_labels"

    id = Column("label_id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False, index=True)
    target_name = Column(String(100), nullable=False)
    label_value = Column(String(100), nullable=False)
    label_source = Column(String(100), nullable=False)
    label_policy_version = Column(String(100), nullable=True)
    reviewer_generator = Column("reviewer_generator", String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

class MLDataset(Base):
    __tablename__ = "ml_datasets"

    id = Column("dataset_id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    dataset_version = Column(String(50), nullable=True)
    label_definition = Column(Text, nullable=True)
    feature_schema_version = Column(String(100), nullable=True)
    label_policy_version = Column(String(100), nullable=True)
    generator_version = Column(String(100), nullable=True)
    frozen_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    source_type = Column(String(50), nullable=True)
    checksum = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)

class MLDatasetMember(Base):
    __tablename__ = "ml_dataset_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id = Column(UUID(as_uuid=True), ForeignKey("ml_datasets.dataset_id"), nullable=False, index=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False)
    feature_snapshot_id = Column(UUID(as_uuid=True), ForeignKey("case_feature_snapshots.id"), nullable=False)
    label_id = Column(UUID(as_uuid=True), ForeignKey("ml_labels.label_id"), nullable=True)
    label = Column(String(50), nullable=True)
    label_source = Column(String(100), nullable=True)
    split = Column(String(50), nullable=False, index=True)
    group_key = Column(String(200), nullable=True)

class ModelVersion(Base):
    __tablename__ = "model_versions"

    id = Column("model_version_id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name = Column(String(200), nullable=True)
    algorithm = Column(String(100), nullable=False)
    dataset_id = Column(UUID(as_uuid=True), ForeignKey("ml_datasets.dataset_id"), nullable=True)
    feature_schema_version = Column(String(100), nullable=True)
    artifact_uri = Column(Text, nullable=True)
    preprocessing_hash = Column(String(255), nullable=True)
    calibrator_uri = Column(Text, nullable=True)
    code_commit = Column(String(100), nullable=True)
    status = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

class ModelTrainingRun(Base):
    __tablename__ = "model_training_runs"

    id = Column("training_run_id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_version_id = Column(UUID(as_uuid=True), ForeignKey("model_versions.model_version_id"), nullable=False)
    dataset_id = Column(UUID(as_uuid=True), ForeignKey("ml_datasets.dataset_id"), nullable=True)
    hyperparameters_json = Column(JSONVariant, nullable=True)
    random_seed = Column(Integer, nullable=True)
    library_versions = Column("environment_json", JSONVariant, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column("completed_at", DateTime(timezone=True), nullable=True)
    status = Column(String(50), nullable=True)

class ModelMetric(Base):
    __tablename__ = "model_metrics"

    id = Column("metric_id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_run_id = Column(UUID(as_uuid=True), ForeignKey("model_training_runs.training_run_id"), nullable=True)
    model_version_id = Column(UUID(as_uuid=True), ForeignKey("model_versions.model_version_id"), nullable=True)
    split = Column(String(50), nullable=False)
    segment_key = Column("slice_name", String(100), nullable=True)
    metric_name = Column(String(100), nullable=False)
    metric_value = Column(Numeric, nullable=False)
    metric_context = Column(JSONVariant, nullable=True)

class ModelDecisionPolicy(Base):
    __tablename__ = "model_decision_policies"

    id = Column("policy_id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_version_id = Column(UUID(as_uuid=True), ForeignKey("model_versions.model_version_id"), nullable=False)
    version = Column(Integer, nullable=True)
    policy_version = Column(String(100), nullable=True)
    accept_threshold = Column("t_accept", Numeric, nullable=True)
    contest_threshold = Column("t_contest", Numeric, nullable=True)
    fp_cost = Column("c_fp", Numeric, nullable=True)
    fn_cost = Column("c_fn", Numeric, nullable=True)
    review_cost = Column("c_review", Numeric, nullable=True)
    hard_block_rules = Column(JSONVariant, nullable=True)
    active = Column(Boolean, nullable=False, default=False)

class RiskPrediction(Base):
    __tablename__ = "risk_predictions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_risk_predictions_idempotency_key"),
    )

    id = Column("prediction_id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False, index=True)
    feature_snapshot_id = Column(UUID(as_uuid=True), ForeignKey("case_feature_snapshots.id"), nullable=False)
    model_version_id = Column(UUID(as_uuid=True), ForeignKey("model_versions.model_version_id"), nullable=False)
    decision_policy_id = Column(UUID(as_uuid=True), ForeignKey("model_decision_policies.policy_id"), nullable=False)
    raw_score = Column(Numeric, nullable=False)
    calibrated_probability = Column(Numeric, nullable=False)
    recommendation = Column(String(50), nullable=False)
    hard_block = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    idempotency_key = Column(String(255), nullable=False)

class PredictionExplanation(Base):
    __tablename__ = "prediction_explanations"

    id = Column("explanation_id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prediction_id = Column(UUID(as_uuid=True), ForeignKey("risk_predictions.prediction_id"), nullable=False)
    rank = Column(Integer, nullable=True)
    feature_name = Column(String(255), nullable=False)
    feature_value = Column("feature_value_safe", JSONVariant, nullable=True)
    contribution = Column("shap_value", Numeric, nullable=True)
    direction = Column(String(50), nullable=True)
    display_text = Column(Text, nullable=True)
