import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Enum, UniqueConstraint, Index, Text, Boolean, Numeric
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector
from app.core.database import Base
from app.models.module_d import JSONVariant

class GSourceStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"

class GSourceType(str, enum.Enum):
    RAZORPAY_POLICY = "RAZORPAY_POLICY"
    INTERNAL_GUIDELINE = "INTERNAL_GUIDELINE"
    REASON_CODE_MAPPING = "REASON_CODE_MAPPING"

class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint("merchant_id", "source_type", "reason_code", "version", name="uq_knowledge_source_identity"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # tenant isolation boundary. NULL means global platform policy.
    merchant_id = Column(UUID(as_uuid=True), ForeignKey("merchants.merchant_id"), nullable=True, index=True)
    source_type = Column(Enum(GSourceType), nullable=False)
    reason_code = Column(String(100), nullable=True)
    title = Column(String(255), nullable=False)
    
    # Checksum is NOT globally unique; used for idempotent checks per tenant/source.
    content_checksum = Column(String(64), nullable=False)
    
    version = Column(Integer, nullable=False)
    status = Column(Enum(GSourceStatus), nullable=False, default=GSourceStatus.DRAFT)
    
    effective_from = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    effective_to = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    metadata_json = Column(JSONVariant, nullable=True)

class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "chunk_index", "embedding_model", "embedding_version", name="uq_knowledge_chunk_identity"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_sources.id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    
    content = Column(String, nullable=False)
    content_checksum = Column(String(64), nullable=False)
    
    embedding_model = Column(String(100), nullable=False, default="text-embedding-3-small")
    embedding_version = Column(Integer, nullable=False, default=1)
    
    # The blueprint and resolved decision mandate pgvector with dimension 1536
    embedding = Column(Vector(1536), nullable=False)
    
    metadata_json = Column(JSONVariant, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    source = relationship("KnowledgeSource", backref="chunks")

class RagRetrievalRun(Base):
    __tablename__ = "rag_retrieval_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False, index=True)
    prediction_id = Column(UUID(as_uuid=True), ForeignKey("risk_predictions.prediction_id"), nullable=False, index=True)
    query_text_redacted = Column(Text, nullable=False)
    filters_json = Column(JSONVariant, nullable=True)
    top_k = Column(Integer, nullable=False)
    index_version = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

class RagRetrievedChunk(Base):
    __tablename__ = "rag_retrieved_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    retrieval_run_id = Column(UUID(as_uuid=True), ForeignKey("rag_retrieval_runs.id"), nullable=False, index=True)
    chunk_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_chunks.id"), nullable=False, index=True)
    rank = Column(Integer, nullable=False)
    similarity_score = Column(Numeric, nullable=False)
    selected_for_prompt = Column(Boolean, nullable=False, default=False)
    selection_reason = Column(Text, nullable=True)

    chunk = relationship("KnowledgeChunk")


# ─── G-05: Context Assembly & LLM Generation (Tables G7-G10) ───────────────

class GenerationStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAILED = "FAILED"

class GuardrailStatus(str, enum.Enum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"

class ClaimType(str, enum.Enum):
    CASE_FACT = "CASE_FACT"
    POLICY = "POLICY"
    RECOMMENDATION = "RECOMMENDATION"

class SupportStatus(str, enum.Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONFLICT = "CONFLICT"

class GuardrailCheckType(str, enum.Enum):
    SCHEMA = "SCHEMA"
    GROUNDING = "GROUNDING"
    CONTRADICTION = "CONTRADICTION"
    PII = "PII"
    PROMPT_INJECTION = "PROMPT_INJECTION"
    CITATION_COVERAGE = "CITATION_COVERAGE"
    LENGTH = "LENGTH"


class ResponseGenerationRun(Base):
    """Table G7 — Tracks one LLM generation attempt per retrieval context."""
    __tablename__ = "response_generation_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False, index=True)
    retrieval_run_id = Column(UUID(as_uuid=True), ForeignKey("rag_retrieval_runs.id"), nullable=True, index=True)
    # Stored as version string (e.g. "v1") — no separate template table required at this stage
    prompt_template_version = Column(String(50), nullable=False)
    # LLM model name (e.g. "gpt-4o-mini")
    llm_model_version = Column(String(100), nullable=False)
    # The version of the guardrail logic applied (G-20 lineage requirement)
    guardrail_version = Column(String(50), nullable=False, default="v1")
    # SHA-256 of serialised fact packet — for reproducibility audit
    fact_packet_hash = Column(String(64), nullable=True)
    status = Column(Enum(GenerationStatus), nullable=False, default=GenerationStatus.RUNNING)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    drafts = relationship("GeneratedDraft", back_populates="generation_run")


class GeneratedDraft(Base):
    """Table G8 — The persisted structured draft produced by the LLM."""
    __tablename__ = "generated_drafts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    generation_run_id = Column(UUID(as_uuid=True), ForeignKey("response_generation_runs.id"), nullable=False, index=True)
    # Denormalised for fast lookup without joining to the run
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False, index=True)
    # Concise generated contest summary (<= 1000 chars per Razorpay limit)
    summary = Column(Text, nullable=False)
    # Suggested contest amount in minor units (may be None for REVIEW/ACCEPT)
    contest_amount_minor = Column(Numeric, nullable=True)
    # Full structured JSON output from the LLM, Pydantic-validated before storage
    draft_json = Column(JSONVariant, nullable=False)
    guardrail_status = Column(Enum(GuardrailStatus), nullable=False, default=GuardrailStatus.REVIEW)
    is_current = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    generation_run = relationship("ResponseGenerationRun", back_populates="drafts")
    claims = relationship("DraftClaim", back_populates="draft", cascade="all, delete-orphan")
    guardrail_results = relationship("LLMGuardrailResult", back_populates="draft", cascade="all, delete-orphan")


class DraftClaim(Base):
    """Table G9 — Atomic factual/policy claims inside a draft, with grounding."""
    __tablename__ = "draft_claims"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    draft_id = Column(UUID(as_uuid=True), ForeignKey("generated_drafts.id"), nullable=False, index=True)
    claim_text = Column(Text, nullable=False)
    claim_type = Column(Enum(ClaimType), nullable=False)
    support_status = Column(Enum(SupportStatus), nullable=False)
    # Structured case fact references (e.g. ["shipment.delivery_time"])
    fact_refs = Column(JSONVariant, nullable=True)
    # RAG source chunk references (e.g. ["chunk_id:source_id"])
    chunk_refs = Column(JSONVariant, nullable=True)

    draft = relationship("GeneratedDraft", back_populates="claims")


class LLMGuardrailResult(Base):
    """Table G10 — Per-check guardrail findings for a generated draft."""
    __tablename__ = "llm_guardrail_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    draft_id = Column(UUID(as_uuid=True), ForeignKey("generated_drafts.id"), nullable=False, index=True)
    check_type = Column(Enum(GuardrailCheckType), nullable=False)
    result = Column(String(20), nullable=False)  # PASS / WARN / FAIL
    details_json = Column(JSONVariant, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    draft = relationship("GeneratedDraft", back_populates="guardrail_results")

# ─── G-19: RAG Evaluation Tables ──────────────────────────────────────────

class RagEvaluationQuery(Base):
    """Table for a small gold retrieval evaluation set (G-19)."""
    __tablename__ = "rag_evaluation_queries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_text = Column(Text, nullable=False)
    # Array of expected valid KnowledgeChunk UUIDs encoded as JSON for this query
    expected_chunk_ids = Column(JSONVariant, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class RagEvaluationRun(Base):
    """Table for persisting deterministic retrieval evaluation results (G-19)."""
    __tablename__ = "rag_evaluation_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    k_value = Column(Integer, nullable=False)
    hit_rate = Column(Numeric, nullable=True)
    precision_at_k = Column(Numeric, nullable=True)
    run_timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
