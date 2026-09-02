import hashlib
import boto3
import re
from dataclasses import dataclass, field
from typing import List
from uuid import UUID
from app.models.module_g import KnowledgeSource, GSourceStatus
from app.core.config import settings

@dataclass
class ChunkDTO:
    chunk_index: int
    content: str
    content_checksum: str
    metadata: dict = field(default_factory=dict)

class MarkdownChunker:
    """
    Deterministically splits Markdown text at ATX heading boundaries.
    Preserves heading text, heading level, and body content.
    """
    
    # Matches ATX headers like "# Heading" up to 6 levels.
    # Group 1: The '#' characters (determines level).
    # Group 2: The heading text.
    HEADER_PATTERN = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)

    @classmethod
    def chunk_content(cls, raw_content: str, source_metadata: dict) -> List[ChunkDTO]:
        """
        Splits content and returns a list of ChunkDTOs.
        """
        # Find all header matches
        matches = list(cls.HEADER_PATTERN.finditer(raw_content))
        
        chunks = []
        
        if not matches:
            # If no headers, the entire content is one chunk
            if raw_content.strip():
                chunks.append(cls._create_dto(
                    content=raw_content.strip(),
                    index=0,
                    source_metadata=source_metadata,
                    section_title="Full Document",
                    heading_level=0
                ))
            return chunks

        # Extract chunks based on header positions
        for i, match in enumerate(matches):
            level = len(match.group(1))
            title = match.group(2).strip()
            
            start_pos = match.start()
            # The chunk goes from this header to the start of the next header (or end of document)
            end_pos = matches[i+1].start() if i + 1 < len(matches) else len(raw_content)
            
            chunk_text = raw_content[start_pos:end_pos].strip()
            
            if chunk_text:
                chunks.append(cls._create_dto(
                    content=chunk_text,
                    index=i,
                    source_metadata=source_metadata,
                    section_title=title,
                    heading_level=level
                ))
                
        return chunks

    @staticmethod
    def _create_dto(content: str, index: int, source_metadata: dict, section_title: str, heading_level: int) -> ChunkDTO:
        checksum = hashlib.sha256(content.encode('utf-8')).hexdigest()
        
        # Merge source metadata with chunk-specific metadata
        chunk_metadata = source_metadata.copy()
        chunk_metadata.update({
            "section_title": section_title,
            "heading_level": heading_level,
            "chunking_strategy": "markdown_header_v1"
        })
        
        return ChunkDTO(
            chunk_index=index,
            content=content,
            content_checksum=checksum,
            metadata=chunk_metadata
        )

class KnowledgeChunkingService:
    def __init__(self, s3_client=None):
        self.s3_client = s3_client or boto3.client(
            's3',
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY
        )

    def _fetch_from_s3(self, source_uri: str) -> str:
        """
        Fetches the raw policy content from MinIO given an s3:// URI.
        """
        if not source_uri.startswith("s3://"):
            raise ValueError(f"Invalid source_uri format: {source_uri}")
        
        # Parse s3://bucket-name/object/key
        parts = source_uri[5:].split('/', 1)
        if len(parts) != 2:
            raise ValueError(f"Malformed source_uri: {source_uri}")
            
        bucket, key = parts
        
        response = self.s3_client.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read()
        return content.decode('utf-8')

    def chunk_source(self, source: KnowledgeSource) -> List[ChunkDTO]:
        """
        Takes a KnowledgeSource, fetches its raw content, and chunks it.
        Returns a list of in-memory ChunkDTO objects.
        """
        # 1. Enforce ACTIVE status boundary
        if source.status != GSourceStatus.ACTIVE:
            raise ValueError("Only ACTIVE KnowledgeSource records can be chunked.")
            
        # 2. Extract MinIO URI
        source_uri = (source.metadata_json or {}).get("source_uri")
        if not source_uri:
            raise ValueError("KnowledgeSource is missing source_uri in metadata_json.")
            
        # 3. Retrieve Raw Content
        raw_content = self._fetch_from_s3(source_uri)
        
        # 4. Prepare base metadata for chunks
        source_metadata = {
            "source_id": str(source.id),
            "source_version": source.version,
            "source_uri": source_uri,
            "tenant_id": str(source.merchant_id) if source.merchant_id else "global"
        }
        
        # 5. Deterministically Chunk
        chunks = MarkdownChunker.chunk_content(raw_content, source_metadata)
        
        return chunks
