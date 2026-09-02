import pytest
import uuid
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from app.core.database import Base
from app.models.module_g import KnowledgeSource, GSourceType, GSourceStatus, KnowledgeChunk
from app.models.shared import Merchant
from app.models.module_a import *
from app.models.module_b import *
from app.models.module_c import *
from app.models.module_d import *
from app.models.module_e import *
from app.services.rag.ingestion import KnowledgeIngestionService

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

# Mock S3 Client
class MockS3Client:
    def __init__(self):
        self.uploads = []
    
    def put_object(self, Bucket, Key, Body, ContentType):
        self.uploads.append({
            "Bucket": Bucket,
            "Key": Key,
            "Body": Body,
            "ContentType": ContentType
        })

@pytest.fixture
def mock_s3():
    return MockS3Client()

@pytest.fixture
def ingestion_service(mock_s3):
    return KnowledgeIngestionService(s3_client=mock_s3)

def test_g01_ingestion_new_source(db_session: Session, ingestion_service: KnowledgeIngestionService, mock_s3: MockS3Client):
    merchant_id = uuid.uuid4()
    content = "This is a merchant return policy."
    title = "Return Policy"
    
    source = ingestion_service.ingest(
        db=db_session,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title=title,
        raw_content=content,
        reason_code="FRAUD",
        merchant_id=merchant_id,
        metadata={"author": "Risk Team"}
    )
    
    assert source.id is not None
    assert source.version == 1
    assert source.status == GSourceStatus.ACTIVE
    assert source.title == title
    assert source.merchant_id == merchant_id
    assert source.reason_code == "FRAUD"
    assert source.effective_from is not None
    assert source.effective_to is None
    assert "source_uri" in source.metadata_json
    assert source.metadata_json["author"] == "Risk Team"
    
    # Verify checksum
    import hashlib
    expected_checksum = hashlib.sha256(content.encode('utf-8')).hexdigest()
    assert source.content_checksum == expected_checksum
    
    # Verify MinIO upload
    assert len(mock_s3.uploads) == 1
    upload = mock_s3.uploads[0]
    assert upload["Body"] == content.encode('utf-8')
    assert str(merchant_id) in upload["Key"]
    assert "s3://" in source.metadata_json["source_uri"]

def test_g01_ingestion_idempotent(db_session: Session, ingestion_service: KnowledgeIngestionService, mock_s3: MockS3Client):
    merchant_id = uuid.uuid4()
    content = "Idempotent content"
    
    source1 = ingestion_service.ingest(
        db=db_session,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title="Title 1",
        raw_content=content,
        merchant_id=merchant_id
    )
    
    # Ingest same content
    source2 = ingestion_service.ingest(
        db=db_session,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title="Title 1 (Ignored)",
        raw_content=content,
        merchant_id=merchant_id
    )
    
    assert source1.id == source2.id
    assert source1.version == source2.version
    assert source1.version == 1
    
    # Only one upload should have occurred
    assert len(mock_s3.uploads) == 1

def test_g01_ingestion_versioning(db_session: Session, ingestion_service: KnowledgeIngestionService, mock_s3: MockS3Client):
    merchant_id = uuid.uuid4()
    
    # Version 1
    source1 = ingestion_service.ingest(
        db=db_session,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title="Policy V1",
        raw_content="Version 1",
        merchant_id=merchant_id,
        reason_code="CHARGEBACK"
    )
    
    # Version 2
    source2 = ingestion_service.ingest(
        db=db_session,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title="Policy V2",
        raw_content="Version 2 Updated",
        merchant_id=merchant_id,
        reason_code="CHARGEBACK"
    )
    
    assert source2.id != source1.id
    assert source2.version == 2
    assert source2.status == GSourceStatus.ACTIVE
    assert source2.effective_from is not None
    assert source2.effective_to is None
    
    # Refresh source1 from DB
    db_session.refresh(source1)
    assert source1.status == GSourceStatus.DEPRECATED
    assert source1.effective_to is not None
    
    # Both versions should be in DB
    all_sources = db_session.query(KnowledgeSource).filter_by(merchant_id=merchant_id).all()
    assert len(all_sources) == 2
    
    # Two uploads occurred
    assert len(mock_s3.uploads) == 2

def test_g01_ingestion_tenant_isolation(db_session: Session, ingestion_service: KnowledgeIngestionService):
    merchant_a = uuid.uuid4()
    merchant_b = uuid.uuid4()
    
    # Merchant A ingests policy
    source_a = ingestion_service.ingest(
        db=db_session,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title="Policy",
        raw_content="A content",
        merchant_id=merchant_a
    )
    
    # Merchant B ingests policy with same type and reason code
    source_b = ingestion_service.ingest(
        db=db_session,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title="Policy",
        raw_content="B content",
        merchant_id=merchant_b
    )
    
    # They should be completely independent (version 1 for both)
    assert source_a.version == 1
    assert source_b.version == 1
    assert source_a.merchant_id == merchant_a
    assert source_b.merchant_id == merchant_b

def test_g01_ingestion_global_policy(db_session: Session, ingestion_service: KnowledgeIngestionService):
    # Global Razorpay Policy
    source_global = ingestion_service.ingest(
        db=db_session,
        source_type=GSourceType.RAZORPAY_POLICY,
        title="Network Rules",
        raw_content="Global rules",
        merchant_id=None
    )
    
    assert source_global.version == 1
    assert source_global.merchant_id is None
    
    # Trying to assign merchant_id to RAZORPAY_POLICY should fail
    with pytest.raises(ValueError, match="RAZORPAY_POLICY is a global policy"):
        ingestion_service.ingest(
            db=db_session,
            source_type=GSourceType.RAZORPAY_POLICY,
            title="Network Rules Invalid",
            raw_content="Global rules",
            merchant_id=uuid.uuid4()
        )

def test_g01_ingestion_no_chunks_created(db_session: Session, ingestion_service: KnowledgeIngestionService):
    merchant_id = uuid.uuid4()
    ingestion_service.ingest(
        db=db_session,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title="Check Chunks",
        raw_content="Some content",
        merchant_id=merchant_id
    )
    
    # Ensure no chunks were created (G-01 boundary)
    chunks = db_session.query(KnowledgeChunk).all()
    assert len(chunks) == 0

def test_g01_ingestion_bytes_input(db_session: Session, ingestion_service: KnowledgeIngestionService, mock_s3: MockS3Client):
    merchant_id = uuid.uuid4()
    content = b"Bytes content \xe2\x9c\x93" # Checkmark
    
    source = ingestion_service.ingest(
        db=db_session,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        title="Bytes test",
        raw_content=content,
        merchant_id=merchant_id
    )
    
    assert source.version == 1
    # Check upload body
    assert mock_s3.uploads[0]["Body"] == content

