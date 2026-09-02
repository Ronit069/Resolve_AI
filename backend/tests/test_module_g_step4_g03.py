import pytest
import uuid
from unittest.mock import MagicMock
from sqlalchemy.exc import IntegrityError

from app.models.module_g import KnowledgeChunk
from app.services.rag.chunking import ChunkDTO
from app.services.rag.embedding import KnowledgeEmbeddingService
from app.core.config import settings

# Database setup using the same pattern as test_module_g_step1.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
import app.models.module_a
import app.models.module_b
import app.models.module_c
import app.models.module_d
import app.models.module_e
import app.models.shared
from app.models.module_g import KnowledgeSource, GSourceType, GSourceStatus
from app.models.shared import Merchant

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_g_embedding.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    merchant = Merchant(merchant_id=uuid.UUID("a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"), name="Test Company", external_merchant_id="test_g03")
    db.add(merchant)
    db.commit()
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture
def active_source(db_session):
    source = KnowledgeSource(
        merchant_id=uuid.UUID("a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"),
        source_type=GSourceType.INTERNAL_GUIDELINE,
        reason_code=f"G03_TEST_{uuid.uuid4().hex[:8]}",
        title="G03 Test Guideline",
        content_checksum="hashXYZ",
        version=1,
        status=GSourceStatus.ACTIVE,
        metadata_json={"source_uri": "s3://test"}
    )
    db_session.add(source)
    db_session.commit()
    return source

@pytest.fixture
def mock_openai_client():
    mock_client = MagicMock()
    return mock_client

@pytest.fixture
def embedding_service(mock_openai_client):
    service = KnowledgeEmbeddingService(client=mock_openai_client)
    service.batch_size = 2  # Small batch size for testing
    return service

def create_mock_response(num_embeddings: int, dimensions: int = 1536):
    class MockData:
        def __init__(self, index, vector):
            self.index = index
            self.embedding = vector
            
    class MockResponse:
        def __init__(self, data):
            self.data = data
            
    data = [MockData(i, [0.1] * dimensions) for i in range(num_embeddings)]
    return MockResponse(data)

def test_g03_basic_embedding_and_persistence(db_session, embedding_service, mock_openai_client, active_source):
    # Setup mock to return exactly 2 embeddings of 1536 dimensions
    mock_openai_client.embeddings.create.return_value = create_mock_response(2, 1536)
    
    source_id_str = str(active_source.id)
    chunks = [
        ChunkDTO(chunk_index=0, content="Content A", content_checksum="hashA", metadata={"source_id": source_id_str}),
        ChunkDTO(chunk_index=1, content="Content B", content_checksum="hashB", metadata={"source_id": source_id_str, "heading_level": 2})
    ]
    
    inserted = embedding_service.process_chunks(db_session, chunks)
    
    assert inserted == 2
    mock_openai_client.embeddings.create.assert_called_once()
    
    # Verify in DB
    db_chunks = db_session.query(KnowledgeChunk).filter(KnowledgeChunk.source_id == active_source.id).order_by(KnowledgeChunk.chunk_index).all()
    assert len(db_chunks) == 2
    
    assert db_chunks[0].chunk_index == 0
    assert db_chunks[0].content == "Content A"
    assert db_chunks[0].embedding_model == "text-embedding-3-small"
    assert db_chunks[0].embedding_version == 1
    assert len(db_chunks[0].embedding) == 1536
    
    assert db_chunks[1].chunk_index == 1
    assert db_chunks[1].metadata_json["heading_level"] == 2

def test_g03_idempotency_zero_api_calls(db_session, embedding_service, mock_openai_client, active_source):
    # Setup mock to return exactly 2 embeddings
    mock_openai_client.embeddings.create.return_value = create_mock_response(2, 1536)
    
    source_id_str = str(active_source.id)
    chunks = [
        ChunkDTO(chunk_index=0, content="Content A", content_checksum="hashA", metadata={"source_id": source_id_str}),
        ChunkDTO(chunk_index=1, content="Content B", content_checksum="hashB", metadata={"source_id": source_id_str, "heading_level": 2})
    ]
    
    # Insert chunks first
    inserted_first = embedding_service.process_chunks(db_session, chunks)
    assert inserted_first == 2
    
    # Pass the SAME chunks again
    mock_openai_client.embeddings.create.reset_mock()
    inserted = embedding_service.process_chunks(db_session, chunks)
    
    assert inserted == 0
    # Crucial: ZERO API calls
    mock_openai_client.embeddings.create.assert_not_called()

def test_g03_partial_persistence(db_session, embedding_service, mock_openai_client, active_source):
    source_id_str = str(active_source.id)
    chunks_initial = [
        ChunkDTO(chunk_index=0, content="Content A", content_checksum="hashA", metadata={"source_id": source_id_str}),
        ChunkDTO(chunk_index=1, content="Content B", content_checksum="hashB", metadata={"source_id": source_id_str}),
    ]
    
    mock_openai_client.embeddings.create.return_value = create_mock_response(2, 1536)
    embedding_service.process_chunks(db_session, chunks_initial)
    
    # Pass chunks 0, 1 (already exist) and 2 (new)
    chunks_partial = chunks_initial + [
        ChunkDTO(chunk_index=2, content="Content C", content_checksum="hashC", metadata={"source_id": source_id_str}),
    ]
    
    mock_openai_client.embeddings.create.reset_mock()
    mock_openai_client.embeddings.create.return_value = create_mock_response(1, 1536)
    
    inserted = embedding_service.process_chunks(db_session, chunks_partial)
    
    assert inserted == 1
    mock_openai_client.embeddings.create.assert_called_once()
    
    # Verify the API was only called with "Content C"
    args, kwargs = mock_openai_client.embeddings.create.call_args
    assert kwargs["input"] == ["Content C"]

def test_g03_invalid_vector_dimension(db_session, embedding_service, mock_openai_client, active_source):
    source_id_str = str(active_source.id)
    chunks = [
        ChunkDTO(chunk_index=3, content="Content D", content_checksum="hashD", metadata={"source_id": source_id_str})
    ]
    
    # Mock returns 1024 dimensions instead of 1536
    mock_openai_client.embeddings.create.return_value = create_mock_response(1, 1024)
    
    with pytest.raises(ValueError, match="Invalid vector dimensionality"):
        embedding_service.process_chunks(db_session, chunks)
        
    # Verify rollback (chunk 3 should not be in DB)
    chunk = db_session.query(KnowledgeChunk).filter(KnowledgeChunk.source_id == active_source.id, KnowledgeChunk.chunk_index == 3).first()
    assert chunk is None

def test_g03_provider_response_count_mismatch(db_session, embedding_service, mock_openai_client, active_source):
    source_id_str = str(active_source.id)
    chunks = [
        ChunkDTO(chunk_index=4, content="Content E", content_checksum="hashE", metadata={"source_id": source_id_str})
    ]
    
    # Mock returns 2 embeddings for 1 input chunk
    mock_openai_client.embeddings.create.return_value = create_mock_response(2, 1536)
    
    with pytest.raises(ValueError, match="Provider returned 2 embeddings, expected 1"):
        embedding_service.process_chunks(db_session, chunks)

def test_g03_batching_behavior(db_session, embedding_service, mock_openai_client, active_source):
    # Set batch size to 2
    embedding_service.batch_size = 2
    source_id_str = str(active_source.id)
    
    chunks = [
        ChunkDTO(chunk_index=5, content="Content 5", content_checksum="hash5", metadata={"source_id": source_id_str}),
        ChunkDTO(chunk_index=6, content="Content 6", content_checksum="hash6", metadata={"source_id": source_id_str}),
        ChunkDTO(chunk_index=7, content="Content 7", content_checksum="hash7", metadata={"source_id": source_id_str}),
    ]
    
    mock_openai_client.embeddings.create.reset_mock()
    # When called, return 2 embeddings first, then 1 embedding
    mock_openai_client.embeddings.create.side_effect = [
        create_mock_response(2, 1536),
        create_mock_response(1, 1536)
    ]
    
    inserted = embedding_service.process_chunks(db_session, chunks)
    
    assert inserted == 3
    assert mock_openai_client.embeddings.create.call_count == 2
    
    # Verify the first call was for 2 chunks, the second for 1
    call1, call2 = mock_openai_client.embeddings.create.call_args_list
    assert len(call1.kwargs["input"]) == 2
    assert len(call2.kwargs["input"]) == 1

def test_g03_cross_tenant_mixing_rejected(db_session, embedding_service):
    chunks = [
        ChunkDTO(chunk_index=0, content="A", content_checksum="hashA", metadata={"source_id": str(uuid.uuid4())}),
        ChunkDTO(chunk_index=1, content="B", content_checksum="hashB", metadata={"source_id": str(uuid.uuid4())})
    ]
    
    with pytest.raises(ValueError, match="Cross-source mixing is not allowed"):
        embedding_service.process_chunks(db_session, chunks)
