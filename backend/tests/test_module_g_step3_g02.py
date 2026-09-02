import pytest
import uuid
import hashlib
from unittest.mock import MagicMock
from app.models.module_g import KnowledgeSource, GSourceType, GSourceStatus
import app.models.module_a
import app.models.module_b
import app.models.module_c
import app.models.module_d
import app.models.module_e
import app.models.shared
from app.services.rag.chunking import KnowledgeChunkingService, MarkdownChunker, ChunkDTO

class MockS3Response:
    def __init__(self, body_bytes):
        self._body = body_bytes
    def read(self):
        return self._body

class MockS3Client:
    def __init__(self):
        self.objects = {}
        self.upload_calls = 0

    def put_object(self, **kwargs):
        self.upload_calls += 1
        key = f"s3://{kwargs['Bucket']}/{kwargs['Key']}"
        self.objects[key] = kwargs['Body']

    def get_object(self, Bucket, Key):
        key = f"s3://{Bucket}/{Key}"
        if key not in self.objects:
            raise Exception(f"NoSuchKey: {key}")
        return {"Body": MockS3Response(self.objects[key])}

@pytest.fixture
def mock_s3():
    return MockS3Client()

@pytest.fixture
def chunking_service(mock_s3):
    return KnowledgeChunkingService(s3_client=mock_s3)

@pytest.fixture
def active_source():
    source = KnowledgeSource(
        id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        source_type=GSourceType.INTERNAL_GUIDELINE,
        version=1,
        status=GSourceStatus.ACTIVE,
        metadata_json={"source_uri": "s3://my-bucket/policies/123.txt"}
    )
    return source

def test_g02_markdown_header_chunking():
    raw_content = """# Heading 1
This is the first section.

## Heading 2
This is a subsection.
It has multiple lines.

### Heading 3

# Another Main Heading
Final content.
"""
    source_metadata = {"source_id": "test", "source_uri": "s3://test"}
    chunks = MarkdownChunker.chunk_content(raw_content, source_metadata)
    
    assert len(chunks) == 4
    
    # Chunk 0
    assert chunks[0].chunk_index == 0
    assert chunks[0].metadata["section_title"] == "Heading 1"
    assert chunks[0].metadata["heading_level"] == 1
    assert "This is the first section." in chunks[0].content
    
    # Chunk 1
    assert chunks[1].chunk_index == 1
    assert chunks[1].metadata["section_title"] == "Heading 2"
    assert chunks[1].metadata["heading_level"] == 2
    assert "This is a subsection." in chunks[1].content
    
    # Chunk 2 (Header only)
    assert chunks[2].chunk_index == 2
    assert chunks[2].metadata["section_title"] == "Heading 3"
    assert chunks[2].metadata["heading_level"] == 3
    assert chunks[2].content == "### Heading 3"
    
    # Chunk 3
    assert chunks[3].chunk_index == 3
    assert chunks[3].metadata["section_title"] == "Another Main Heading"
    assert chunks[3].metadata["heading_level"] == 1
    assert "Final content." in chunks[3].content

def test_g02_checksum_determinism():
    raw_content = "# Title\nSome content."
    source_metadata = {"source_id": "test"}
    
    chunks1 = MarkdownChunker.chunk_content(raw_content, source_metadata)
    chunks2 = MarkdownChunker.chunk_content(raw_content, source_metadata)
    
    assert chunks1[0].content_checksum == chunks2[0].content_checksum
    
    expected_hash = hashlib.sha256(chunks1[0].content.encode('utf-8')).hexdigest()
    assert chunks1[0].content_checksum == expected_hash

def test_g02_no_headers_fallback():
    raw_content = "This is a document with no headers.\nJust plain text."
    source_metadata = {}
    chunks = MarkdownChunker.chunk_content(raw_content, source_metadata)
    
    assert len(chunks) == 1
    assert chunks[0].metadata["section_title"] == "Full Document"
    assert chunks[0].metadata["heading_level"] == 0
    assert chunks[0].content == raw_content.strip()

def test_g02_empty_content():
    raw_content = "   \n  "
    chunks = MarkdownChunker.chunk_content(raw_content, {})
    assert len(chunks) == 0

def test_g02_unicode_handling():
    raw_content = "# 测试 Heading\nUnicode content ✓"
    chunks = MarkdownChunker.chunk_content(raw_content, {})
    assert len(chunks) == 1
    assert chunks[0].metadata["section_title"] == "测试 Heading"
    assert "Unicode content ✓" in chunks[0].content
    expected_hash = hashlib.sha256(chunks[0].content.encode('utf-8')).hexdigest()
    assert chunks[0].content_checksum == expected_hash

def test_g02_service_integration(chunking_service, mock_s3, active_source):
    # Setup mock S3 data
    mock_s3.objects["s3://my-bucket/policies/123.txt"] = b"# Policy\nLine 1"
    
    chunks = chunking_service.chunk_source(active_source)
    
    assert len(chunks) == 1
    assert chunks[0].content == "# Policy\nLine 1"
    assert chunks[0].metadata["source_id"] == str(active_source.id)
    assert chunks[0].metadata["source_version"] == 1
    assert chunks[0].metadata["source_uri"] == "s3://my-bucket/policies/123.txt"
    assert chunks[0].metadata["chunking_strategy"] == "markdown_header_v1"
    assert chunks[0].metadata["tenant_id"] == str(active_source.merchant_id)
    
    # Ensure no uploads occurred during chunking
    assert mock_s3.upload_calls == 0

def test_g02_deprecated_source_rejection(chunking_service, active_source):
    active_source.status = GSourceStatus.DEPRECATED
    with pytest.raises(ValueError, match="Only ACTIVE KnowledgeSource records can be chunked"):
        chunking_service.chunk_source(active_source)

def test_g02_missing_uri(chunking_service, active_source):
    active_source.metadata_json = {}
    with pytest.raises(ValueError, match="KnowledgeSource is missing source_uri"):
        chunking_service.chunk_source(active_source)

def test_g02_tenant_inheritance(chunking_service, mock_s3):
    merchant_id = uuid.uuid4()
    source = KnowledgeSource(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        source_type=GSourceType.INTERNAL_GUIDELINE,
        version=1,
        status=GSourceStatus.ACTIVE,
        metadata_json={"source_uri": "s3://bucket/test.txt"}
    )
    mock_s3.objects["s3://bucket/test.txt"] = b"# T\nContent"
    
    chunks = chunking_service.chunk_source(source)
    assert chunks[0].metadata["tenant_id"] == str(merchant_id)
    
    # Global policy
    source_global = KnowledgeSource(
        id=uuid.uuid4(),
        merchant_id=None,
        source_type=GSourceType.RAZORPAY_POLICY,
        version=1,
        status=GSourceStatus.ACTIVE,
        metadata_json={"source_uri": "s3://bucket/global.txt"}
    )
    mock_s3.objects["s3://bucket/global.txt"] = b"# G\nGlobal"
    
    chunks_global = chunking_service.chunk_source(source_global)
    assert chunks_global[0].metadata["tenant_id"] == "global"
