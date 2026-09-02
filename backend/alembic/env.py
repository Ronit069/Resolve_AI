from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.shared import Base
from app.models.module_a import WebhookEvent, Dispute, DisputeEvent
from app.models.module_b import Payment, Order, Shipment, Refund, CustomerHistory, CaseEnrichment
from app.models.module_c import EvidenceDocument, MalwareScanResult, EvidenceRequirement, CaseEvidenceStatus, EvidenceAccessEvent
from app.models.module_d import DocumentProcessingJob, DocumentPage, DocumentExtraction, ExtractedField, DocumentQualityAssessment, DocumentModelVersion, CaseDocumentIntelligenceStatus
from app.models.module_e import EvidencePolicyVersion, EvidenceValidationRun, ValidationRuleCatalog, ValidationRuleVersion, EvidenceValidationResult, EvidenceRequirementAssessment, CrossSourceFieldLink, FeatureDefinition, CaseFeatureSnapshot
from app.models.module_g import (
    KnowledgeSource, KnowledgeChunk, RagRetrievalRun, RagRetrievedChunk,
    ResponseGenerationRun, GeneratedDraft, DraftClaim, LLMGuardrailResult
)
from app.models.module_f import MLLabel, MLDataset, MLDatasetMember, ModelVersion, ModelTrainingRun, ModelMetric, ModelDecisionPolicy, RiskPrediction, PredictionExplanation
from app.models.module_h import (
    ReviewQueueItem, ReviewAction, ContestPackage, ContestPackageDocument,
    RazorpayDocumentLink, ExternalActionOutbox, ExternalActionAttempt,
    ContestSubmission, DisputeOutcome, CuratedFeedbackLabel
)
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


from app.core.config import settings

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section)
    url_from_config = config.get_main_option("sqlalchemy.url")
    configuration["sqlalchemy.url"] = url_from_config or settings.DATABASE_URL
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
