import pytest
import uuid
import numpy as np
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List

from app.models.shared import Merchant, Case
from app.models.module_a import *
from app.models.module_b import *
from app.models.module_c import *
from app.models.module_d import *
from app.models.module_e import *
from app.models.module_f import ModelVersion, ModelDecisionPolicy, RiskPrediction
from app.models.module_g import KnowledgeSource, KnowledgeChunk, GSourceStatus, GSourceType, RagRetrievalRun, RagRetrievedChunk
from app.services.rag.retrieval import KnowledgeRetrievalService, RetrievedChunkDTO
from app.core.database import SessionLocal

@pytest.fixture
def db_session():
    # Use actual PostgreSQL SessionLocal for pgvector tests
    session = SessionLocal()
    yield session
    session.close()

from sqlalchemy import text

@pytest.fixture(autouse=True)
def cleanup_db(db_session: Session):
    db_session.execute(text("TRUNCATE TABLE rag_retrieved_chunks CASCADE;"))
    db_session.execute(text("TRUNCATE TABLE rag_retrieval_runs CASCADE;"))
    db_session.execute(text("TRUNCATE TABLE knowledge_chunks CASCADE;"))
    db_session.execute(text("TRUNCATE TABLE knowledge_sources CASCADE;"))
    db_session.commit()

# Utility to create 1536-dim vector
def create_vector(value: float) -> List[float]:
    return [value] * 1536

@pytest.fixture
def f18_dependencies(db_session: Session):
    merchant = Merchant(name="Test Merchant G4", external_merchant_id=f"ext_g4_{uuid.uuid4()}")
    db_session.add(merchant)
    db_session.flush()

    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_g4_{uuid.uuid4()}", source="synthetic")
    db_session.add(case)
    db_session.flush()
    
    policy_version = EvidencePolicyVersion(payment_network="visa", reason_code=f"10.4_{uuid.uuid4()}", phase="PRE", version=1, effective_from=datetime.now(timezone.utc))
    db_session.add(policy_version)
    db_session.flush()

    validation_run = EvidenceValidationRun(
        case_id=case.case_id, 
        policy_version_id=policy_version.policy_version_id, 
        status="COMPLETED",
        evidence_version="v1",
        started_at=datetime.now(timezone.utc),
        idempotency_key=f"val_key_{uuid.uuid4()}"
    )
    db_session.add(validation_run)
    db_session.flush()

    feature_snapshot = CaseFeatureSnapshot(
        case_id=case.case_id,
        validation_run_id=validation_run.id,
        feature_schema_version="v1",
        features_json={"f1": 1},
        feature_hash=f"hash_g4_{uuid.uuid4()}"
    )
    db_session.add(feature_snapshot)
    
    model_version = ModelVersion(algorithm="catboost", status="ACTIVE")
    db_session.add(model_version)
    db_session.flush()
    
    decision_policy = ModelDecisionPolicy(model_version_id=model_version.id, active=True)
    db_session.add(decision_policy)
    db_session.flush()
    
    prediction = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=feature_snapshot.id,
        model_version_id=model_version.id,
        decision_policy_id=decision_policy.id,
        raw_score=0.85,
        calibrated_probability=0.82,
        recommendation="REVIEW",
        hard_block=False,
        idempotency_key=f"key_g4_test_{uuid.uuid4()}"
    )
    db_session.add(prediction)
    db_session.flush()
    
    return {
        "merchant": merchant,
        "case": case,
        "prediction": prediction
    }

@pytest.fixture
def retrieval_service(db_session: Session):
    return KnowledgeRetrievalService(db_session)


def test_invalid_vector_dimension(db_session: Session, retrieval_service: KnowledgeRetrievalService, f18_dependencies):
    case = f18_dependencies["case"]
    prediction = f18_dependencies["prediction"]
    
    bad_vector = [0.1] * 10 # only 10 dims
    
    with pytest.raises(ValueError, match="exactly 1536 dimensions"):
        retrieval_service.retrieve_knowledge(
            case_id=case.case_id,
            prediction_id=prediction.id,
            query_embedding=bad_vector,
            query_text_redacted="test query",
            top_k=5
        )
    # Ensure no audit record was created due to error
    assert db_session.query(RagRetrievalRun).count() == 0

def test_tenant_isolation(db_session: Session, retrieval_service: KnowledgeRetrievalService, f18_dependencies):
    merchant_a = f18_dependencies["merchant"]
    case = f18_dependencies["case"]
    prediction = f18_dependencies["prediction"]
    
    merchant_b = Merchant(name="Test Merchant B", external_merchant_id=f"ext_b_{uuid.uuid4()}")
    db_session.add(merchant_b)
    db_session.flush()
    
    # Global Policy
    global_source = KnowledgeSource(
        source_type=GSourceType.RAZORPAY_POLICY,
        title="Global Policy",
        content_checksum="chk1",
        version=1,
        status=GSourceStatus.ACTIVE
    )
    db_session.add(global_source)
    db_session.flush()
    
    global_chunk = KnowledgeChunk(source_id=global_source.id, chunk_index=0, content="Global content", content_checksum="cchk1", embedding=create_vector(0.1))
    db_session.add(global_chunk)

    # Merchant A Policy
    ma_source = KnowledgeSource(
        merchant_id=merchant_a.merchant_id,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title="MA Policy",
        content_checksum="chk2",
        version=1,
        status=GSourceStatus.ACTIVE
    )
    db_session.add(ma_source)
    db_session.flush()
    
    ma_chunk = KnowledgeChunk(source_id=ma_source.id, chunk_index=0, content="MA content", content_checksum="cchk2", embedding=create_vector(0.1))
    db_session.add(ma_chunk)

    # Merchant B Policy
    mb_source = KnowledgeSource(
        merchant_id=merchant_b.merchant_id,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title="MB Policy",
        content_checksum="chk3",
        version=1,
        status=GSourceStatus.ACTIVE
    )
    db_session.add(mb_source)
    db_session.flush()
    
    mb_chunk = KnowledgeChunk(source_id=mb_source.id, chunk_index=0, content="MB content", content_checksum="cchk3", embedding=create_vector(0.1))
    db_session.add(mb_chunk)
    db_session.flush()

    # Retrieve for Merchant A
    # The dummy vector has cosine distance of 0.0 with another [0.1]*1536 vector, so sim_score = 1.0 > 0.70 threshold.
    results = retrieval_service.retrieve_knowledge(
        case_id=case.case_id,
        prediction_id=prediction.id,
        query_embedding=create_vector(0.1),
        query_text_redacted="query",
        merchant_id=merchant_a.merchant_id,
        top_k=5
    )

    retrieved_content = [r.content for r in results]
    assert "Global content" in retrieved_content
    assert "MA content" in retrieved_content
    assert "MB content" not in retrieved_content # Cross-tenant leakage prevention

def test_reason_code_priority(db_session: Session, retrieval_service: KnowledgeRetrievalService, f18_dependencies):
    merchant = f18_dependencies["merchant"]
    case = f18_dependencies["case"]
    prediction = f18_dependencies["prediction"]
    
    # Exact reason code match with lower similarity (distance 0.2, sim 0.8)
    exact_source = KnowledgeSource(
        merchant_id=merchant.merchant_id,
        reason_code="10.4",
        source_type=GSourceType.RAZORPAY_POLICY,
        title="Exact Policy",
        content_checksum="exchk",
        version=1,
        status=GSourceStatus.ACTIVE
    )
    db_session.add(exact_source)
    db_session.flush()
    exact_chunk = KnowledgeChunk(source_id=exact_source.id, chunk_index=0, content="Exact match content", content_checksum="c_ex", embedding=create_vector(0.2))
    db_session.add(exact_chunk)

    # Generic reason code match with high similarity (distance 0.0, sim 1.0)
    generic_source = KnowledgeSource(
        merchant_id=merchant.merchant_id,
        reason_code=None,
        source_type=GSourceType.RAZORPAY_POLICY,
        title="Generic Policy",
        content_checksum="genchk",
        version=1,
        status=GSourceStatus.ACTIVE
    )
    db_session.add(generic_source)
    db_session.flush()
    generic_chunk = KnowledgeChunk(source_id=generic_source.id, chunk_index=0, content="Generic match content", content_checksum="c_gen", embedding=create_vector(0.1))
    db_session.add(generic_chunk)
    db_session.flush()

    # Query with vector [0.1]*1536. 
    # Exact chunk dist will be > 0.0 (lower similarity), generic chunk will be 0.0 (high similarity)
    results = retrieval_service.retrieve_knowledge(
        case_id=case.case_id,
        prediction_id=prediction.id,
        query_embedding=create_vector(0.1),
        query_text_redacted="query",
        merchant_id=merchant.merchant_id,
        reason_code="10.4",
        top_k=5
    )

    assert len(results) == 2
    # Exact MUST be ranked first despite having lower semantic similarity
    assert results[0].content == "Exact match content"
    assert results[0].reason_code == "10.4"
    assert results[1].content == "Generic match content"

def test_active_effective_filtering(db_session: Session, retrieval_service: KnowledgeRetrievalService, f18_dependencies):
    case = f18_dependencies["case"]
    prediction = f18_dependencies["prediction"]
    
    now = datetime.now(timezone.utc)
    
    # 1. DRAFT exclusion
    draft_src = KnowledgeSource(source_type=GSourceType.INTERNAL_GUIDELINE, title="Draft", content_checksum="d", version=1, status=GSourceStatus.DRAFT)
    db_session.add(draft_src)
    db_session.flush()
    db_session.add(KnowledgeChunk(source_id=draft_src.id, chunk_index=0, content="draft", content_checksum="d", embedding=create_vector(0.1)))

    # 2. DEPRECATED exclusion
    depr_src = KnowledgeSource(source_type=GSourceType.INTERNAL_GUIDELINE, title="Depr", content_checksum="dp", version=1, status=GSourceStatus.DEPRECATED)
    db_session.add(depr_src)
    db_session.flush()
    db_session.add(KnowledgeChunk(source_id=depr_src.id, chunk_index=0, content="depr", content_checksum="dp", embedding=create_vector(0.1)))

    # 3. Expired exclusion
    exp_src = KnowledgeSource(source_type=GSourceType.INTERNAL_GUIDELINE, title="Exp", content_checksum="ex", version=1, status=GSourceStatus.ACTIVE, effective_to=now - timedelta(days=1))
    db_session.add(exp_src)
    db_session.flush()
    db_session.add(KnowledgeChunk(source_id=exp_src.id, chunk_index=0, content="expired", content_checksum="ex", embedding=create_vector(0.1)))

    # 4. Future effective exclusion
    fut_src = KnowledgeSource(source_type=GSourceType.INTERNAL_GUIDELINE, title="Fut", content_checksum="fu", version=1, status=GSourceStatus.ACTIVE, effective_from=now + timedelta(days=1))
    db_session.add(fut_src)
    db_session.flush()
    db_session.add(KnowledgeChunk(source_id=fut_src.id, chunk_index=0, content="future", content_checksum="fu", embedding=create_vector(0.1)))

    # 5. Valid Active
    valid_src = KnowledgeSource(source_type=GSourceType.INTERNAL_GUIDELINE, title="Val", content_checksum="val", version=1, status=GSourceStatus.ACTIVE, effective_from=now - timedelta(days=1))
    db_session.add(valid_src)
    db_session.flush()
    db_session.add(KnowledgeChunk(source_id=valid_src.id, chunk_index=0, content="valid", content_checksum="val", embedding=create_vector(0.1)))

    db_session.flush()

    results = retrieval_service.retrieve_knowledge(
        case_id=case.case_id,
        prediction_id=prediction.id,
        query_embedding=create_vector(0.1),
        query_text_redacted="q",
        top_k=5
    )

    assert len(results) == 1
    assert results[0].content == "valid"

def test_metadata_filtering(db_session: Session, retrieval_service: KnowledgeRetrievalService, f18_dependencies):
    case = f18_dependencies["case"]
    prediction = f18_dependencies["prediction"]
    
    src1 = KnowledgeSource(source_type=GSourceType.INTERNAL_GUIDELINE, title="Visa", content_checksum="v1", version=1, status=GSourceStatus.ACTIVE, metadata_json={"payment_network": "visa", "phase": "PRE"})
    db_session.add(src1)
    db_session.flush()
    db_session.add(KnowledgeChunk(source_id=src1.id, chunk_index=0, content="visa match", content_checksum="c1", embedding=create_vector(0.1)))

    src2 = KnowledgeSource(source_type=GSourceType.INTERNAL_GUIDELINE, title="Mastercard", content_checksum="v2", version=1, status=GSourceStatus.ACTIVE, metadata_json={"payment_network": "mastercard", "phase": "PRE"})
    db_session.add(src2)
    db_session.flush()
    db_session.add(KnowledgeChunk(source_id=src2.id, chunk_index=0, content="mc match", content_checksum="c2", embedding=create_vector(0.1)))

    db_session.flush()

    results = retrieval_service.retrieve_knowledge(
        case_id=case.case_id,
        prediction_id=prediction.id,
        query_embedding=create_vector(0.1),
        query_text_redacted="q",
        metadata_filters={"payment_network": "visa"},
        top_k=5
    )

    assert len(results) == 1
    assert results[0].content == "visa match"

def test_audit_creation_and_transaction_rollback(db_session: Session, retrieval_service: KnowledgeRetrievalService, f18_dependencies):
    case = f18_dependencies["case"]
    prediction = f18_dependencies["prediction"]
    
    # 1. Successful run
    src = KnowledgeSource(source_type=GSourceType.INTERNAL_GUIDELINE, title="Test", content_checksum="t", version=1, status=GSourceStatus.ACTIVE)
    db_session.add(src)
    db_session.flush()
    chk = KnowledgeChunk(source_id=src.id, chunk_index=0, content="content", content_checksum="c", embedding=create_vector(0.1))
    db_session.add(chk)
    db_session.flush()
    
    initial_runs = db_session.query(RagRetrievalRun).count()

    results = retrieval_service.retrieve_knowledge(
        case_id=case.case_id,
        prediction_id=prediction.id,
        query_embedding=create_vector(0.1),
        query_text_redacted="audit_test",
        top_k=5
    )

    assert len(results) == 1
    assert db_session.query(RagRetrievalRun).count() == initial_runs + 1
    run = db_session.query(RagRetrievalRun).order_by(RagRetrievalRun.created_at.desc()).first()
    assert run.query_text_redacted == "audit_test"
    assert run.case_id == case.case_id
    assert run.prediction_id == prediction.id
    
    retrieved_chunks_audit = db_session.query(RagRetrievedChunk).filter_by(retrieval_run_id=run.id).all()
    assert len(retrieved_chunks_audit) == 1
    assert retrieved_chunks_audit[0].chunk_id == chk.id
    
    # 2. Transaction Rollback evaluation
    # If case_id is an invalid UUID (which raises exception during db.add/flush), check rollback
    with pytest.raises(Exception):
        retrieval_service.retrieve_knowledge(
            case_id="INVALID_UUID", 
            prediction_id=prediction.id,
            query_embedding=create_vector(0.1),
            query_text_redacted="fail_test",
            top_k=5
        )
    # The run count should still be the same (rolled back)
    assert db_session.query(RagRetrievalRun).count() == initial_runs + 1

def test_empty_results_handling(db_session: Session, retrieval_service: KnowledgeRetrievalService, f18_dependencies):
    case = f18_dependencies["case"]
    prediction = f18_dependencies["prediction"]
    
    # Clean up any generic knowledge chunks to guarantee 0 matches
    # db_session.query(KnowledgeChunk).delete()
    db_session.flush()

    results = retrieval_service.retrieve_knowledge(
        case_id=case.case_id,
        prediction_id=prediction.id,
        query_embedding=create_vector(0.1),
        query_text_redacted="empty_test",
        top_k=5
    )

    assert len(results) == 0
    run = db_session.query(RagRetrievalRun).order_by(RagRetrievalRun.created_at.desc()).first()
    assert run is not None
    # No chunk audits
    assert db_session.query(RagRetrievedChunk).filter_by(retrieval_run_id=run.id).count() == 0

def test_zero_openai_calls(mocker, db_session: Session, retrieval_service: KnowledgeRetrievalService, f18_dependencies):
    # Tests MUST explicitly prove that no OpenAI client is called
    openai_mock = mocker.patch("openai.OpenAI", autospec=True)
    
    case = f18_dependencies["case"]
    prediction = f18_dependencies["prediction"]
    
    retrieval_service.retrieve_knowledge(
        case_id=case.case_id,
        prediction_id=prediction.id,
        query_embedding=create_vector(0.1),
        query_text_redacted="openai_test",
        top_k=5
    )
    
    assert not openai_mock.called, "OpenAI client must not be instantiated by G-04 Retrieval"

def test_similarity_threshold_boundary(db_session: Session, retrieval_service: KnowledgeRetrievalService, f18_dependencies):
    merchant = f18_dependencies['merchant']
    case = f18_dependencies['case']
    prediction = f18_dependencies['prediction']
    
    import math
    def make_vec(sim: float):
        v = [0.0]*1536
        v[0] = sim
        v[1] = math.sqrt(1.0 - sim*sim)
        return v
        
    query_vec = [0.0]*1536
    query_vec[0] = 1.0

    s = KnowledgeSource(
        merchant_id=merchant.merchant_id,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title='Threshold Source',
        content_checksum='thresh',
        version=1,
        status=GSourceStatus.ACTIVE
    )
    db_session.add(s)
    db_session.flush()

    # Chunk 1: Similarity > 0.70 (e.g. 0.80)
    c1 = KnowledgeChunk(source_id=s.id, chunk_index=1, content='c1', content_checksum='c1', embedding=make_vec(0.80))
    # Chunk 2: Similarity == 0.70
    c2 = KnowledgeChunk(source_id=s.id, chunk_index=2, content='c2', content_checksum='c2', embedding=make_vec(0.70))
    # Chunk 3: Similarity < 0.70 (e.g. 0.69)
    c3 = KnowledgeChunk(source_id=s.id, chunk_index=3, content='c3', content_checksum='c3', embedding=make_vec(0.69))
    
    db_session.add_all([c1, c2, c3])
    db_session.flush()

    results = retrieval_service.retrieve_knowledge(
        case_id=case.case_id,
        prediction_id=prediction.id,
        query_embedding=query_vec,
        query_text_redacted='thresh query',
        merchant_id=merchant.merchant_id,
        top_k=5
    )
    
    retrieved_content = [r.content for r in results]
    assert 'c1' in retrieved_content, 'Chunk > 0.70 should be included'
    assert 'c2' in retrieved_content, 'Chunk == 0.70 should be included per inclusive boundary'
    assert 'c3' not in retrieved_content, 'Chunk < 0.70 should be excluded'

