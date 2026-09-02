import pytest
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
from app.models.module_g import KnowledgeSource, KnowledgeChunk, GSourceType, GSourceStatus
from app.models.shared import Merchant
from app.models.module_a import *
from app.models.module_b import *
from app.models.module_c import *
from app.models.module_d import *
import uuid
from app.models.module_e import *
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_g.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(scope="module", autouse=True)
def setup_tenant():
    db = TestingSessionLocal()
    # Ensure foreign key constraints for merchant_id
    merchant = Merchant(merchant_id=uuid.UUID("a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"), name="Test Company", external_merchant_id="test1")
    merchant2 = Merchant(merchant_id=uuid.UUID("b2b2b2b2-b2b2-b2b2-b2b2-b2b2b2b2b2b2"), name="Test Company 2", external_merchant_id="test2")
    merchant3 = Merchant(merchant_id=uuid.UUID("c3c3c3c3-c3c3-c3c3-c3c3-c3c3c3c3c3c3"), name="Test Company 3", external_merchant_id="test3")
    merchant4 = Merchant(merchant_id=uuid.UUID("d4d4d4d4-d4d4-d4d4-d4d4-d4d4d4d4d4d4"), name="Test Company 4", external_merchant_id="test4")
    db.add(merchant)
    db.add(merchant2)
    db.add(merchant3)
    db.add(merchant4)
    db.commit()
    db.close()


def test_knowledge_source_model_creation(db_session):
    # Test creation and required fields
    source = KnowledgeSource(
        merchant_id=uuid.UUID("a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"),
        source_type=GSourceType.RAZORPAY_POLICY,
        reason_code="CHARGEBACK_1",
        title="Dispute Guideline",
        content_checksum="hash123",
        version=1,
        status=GSourceStatus.ACTIVE,
        metadata_json={"author": "resolve_ai"}
    )
    db_session.add(source)
    db_session.commit()
    
    assert source.id is not None
    assert str(source.merchant_id) == "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"
    assert source.status == GSourceStatus.ACTIVE


def test_knowledge_source_unique_constraint(db_session):
    source1 = KnowledgeSource(
        merchant_id=uuid.UUID("b2b2b2b2-b2b2-b2b2-b2b2-b2b2b2b2b2b2"),
        source_type=GSourceType.INTERNAL_GUIDELINE,
        reason_code="POLICY_X",
        title="Guideline 1",
        content_checksum="hashXYZ",
        version=1,
    )
    db_session.add(source1)
    db_session.commit()

    source2 = KnowledgeSource(
        merchant_id=uuid.UUID("b2b2b2b2-b2b2-b2b2-b2b2-b2b2b2b2b2b2"),
        source_type=GSourceType.INTERNAL_GUIDELINE,
        reason_code="POLICY_X",
        title="Guideline 1 Duplicate",
        content_checksum="hashXYZ_diff",
        version=1,
    )
    db_session.add(source2)
    
    with pytest.raises(IntegrityError):
        db_session.commit()
    
    db_session.rollback()


def test_knowledge_chunk_model_creation(db_session):
    source = KnowledgeSource(
        merchant_id=uuid.UUID("c3c3c3c3-c3c3-c3c3-c3c3-c3c3c3c3c3c3"),
        source_type=GSourceType.RAZORPAY_POLICY,
        title="Test chunk source",
        content_checksum="hash456",
        version=1
    )
    db_session.add(source)
    db_session.commit()

    # Create dummy 1536-dim vector
    dummy_vector = [0.1] * 1536

    chunk = KnowledgeChunk(
        source_id=source.id,
        chunk_index=0,
        content="This is a test chunk.",
        content_checksum="chunkhash1",
        embedding_model="text-embedding-3-small",
        embedding=dummy_vector
    )
    db_session.add(chunk)
    db_session.commit()

    assert chunk.id is not None
    assert chunk.embedding_model == "text-embedding-3-small"
    assert len(chunk.embedding) == 1536
    assert chunk.source_id == source.id


def test_knowledge_chunk_unique_constraint(db_session):
    source = KnowledgeSource(
        merchant_id=uuid.UUID("d4d4d4d4-d4d4-d4d4-d4d4-d4d4d4d4d4d4"),
        source_type=GSourceType.RAZORPAY_POLICY,
        title="Test chunk source 2",
        content_checksum="hash789",
        version=1
    )
    db_session.add(source)
    db_session.commit()

    dummy_vector = [0.0] * 1536

    chunk1 = KnowledgeChunk(
        source_id=source.id,
        chunk_index=0,
        content="Chunk 1",
        content_checksum="chunkhash1",
        embedding=dummy_vector
    )
    db_session.add(chunk1)
    db_session.commit()

    chunk2 = KnowledgeChunk(
        source_id=source.id,
        chunk_index=0,
        content="Chunk 2 (Duplicate index)",
        content_checksum="chunkhash2",
        embedding=dummy_vector
    )
    db_session.add(chunk2)
    
    with pytest.raises(IntegrityError):
        db_session.commit()
    
    db_session.rollback()
