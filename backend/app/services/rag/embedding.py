from typing import List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_
from openai import OpenAI
import openai

from app.core.config import settings
from app.models.module_g import KnowledgeChunk
from app.services.rag.chunking import ChunkDTO

class KnowledgeEmbeddingService:
    def __init__(self, client: OpenAI = None):
        # Allow passing a mocked client for testing. 
        # By default, use settings which will use retries (default max_retries=2).
        self.client = client or OpenAI(api_key=settings.OPENAI_API_KEY)
        self.embedding_model = "text-embedding-3-small"
        self.embedding_version = 1
        self.dimensions = 1536
        self.batch_size = settings.EMBEDDING_BATCH_SIZE

    def process_chunks(self, db: Session, chunks: List[ChunkDTO]) -> int:
        """
        Takes a list of ChunkDTOs, identifies missing ones in the DB,
        batches them, fetches embeddings, and persists them safely.
        Returns the number of new chunks inserted.
        """
        if not chunks:
            return 0

        # Enforce all chunks belong to the same source for sanity
        import uuid
        source_id_str = chunks[0].metadata.get("source_id")
        if not source_id_str:
            raise ValueError("Missing source_id in chunk metadata")
        
        try:
            source_id = uuid.UUID(source_id_str)
        except ValueError:
            raise ValueError(f"Invalid source_id format: {source_id_str}")
            
        for c in chunks:
            if c.metadata.get("source_id") != source_id_str:
                raise ValueError("Cross-source mixing is not allowed in a single batch")

        # 1. Idempotency Check: find which chunk_indexes already exist
        chunk_indexes = [c.chunk_index for c in chunks]
        existing_chunks = db.query(KnowledgeChunk.chunk_index).filter(
            and_(
                KnowledgeChunk.source_id == source_id,
                KnowledgeChunk.embedding_model == self.embedding_model,
                KnowledgeChunk.embedding_version == self.embedding_version,
                KnowledgeChunk.chunk_index.in_(chunk_indexes)
            )
        ).all()
        
        existing_indexes = {row[0] for row in existing_chunks}
        
        # 2. Filter out already existing chunks
        missing_chunks = [c for c in chunks if c.chunk_index not in existing_indexes]
        
        if not missing_chunks:
            # Zero API calls if all already exist
            return 0
            
        # 3. Batch process missing chunks
        total_inserted = 0
        try:
            for i in range(0, len(missing_chunks), self.batch_size):
                batch = missing_chunks[i:i + self.batch_size]
                
                # Extract text for API call
                texts = [c.content for c in batch]
                
                # Call OpenAI
                response = self.client.embeddings.create(
                    input=texts,
                    model=self.embedding_model,
                    dimensions=self.dimensions
                )
                
                # Validate response count
                if len(response.data) != len(batch):
                    raise ValueError(f"Provider returned {len(response.data)} embeddings, expected {len(batch)}")
                
                # Re-associate and validate dimensions
                db_objects = []
                # Ensure ordered mapping by data.index
                sorted_data = sorted(response.data, key=lambda x: x.index)
                
                for chunk_dto, embedding_data in zip(batch, sorted_data):
                    vector = embedding_data.embedding
                    
                    if len(vector) != self.dimensions:
                        raise ValueError(f"Invalid vector dimensionality: expected {self.dimensions}, got {len(vector)}")
                    
                    # Create the database record
                    new_chunk = KnowledgeChunk(
                        source_id=source_id,
                        chunk_index=chunk_dto.chunk_index,
                        content=chunk_dto.content,
                        content_checksum=chunk_dto.content_checksum,
                        embedding_model=self.embedding_model,
                        embedding_version=self.embedding_version,
                        embedding=vector,
                        metadata_json=chunk_dto.metadata
                    )
                    db_objects.append(new_chunk)
                
                # Bulk insert batch
                db.add_all(db_objects)
                db.flush()
                total_inserted += len(db_objects)
                
            db.commit()
            return total_inserted
            
        except Exception as e:
            db.rollback()
            raise e
