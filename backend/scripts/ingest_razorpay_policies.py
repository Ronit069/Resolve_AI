import os
import sys

# Add the backend directory to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
import app.models.shared
import app.models.module_a
import app.models.module_b
import app.models.module_c
import app.models.module_d
import app.models.module_e
import app.models.module_f
import app.models.module_g
import app.models.module_h
from app.models.module_g import GSourceType
from app.services.rag.ingestion import KnowledgeIngestionService
from app.services.rag.chunking import KnowledgeChunkingService
from app.services.rag.embedding import KnowledgeEmbeddingService

def main():
    db = SessionLocal()
    
    try:
        # 1. Read the Markdown file
        file_path = os.path.join(os.path.dirname(__file__), "data", "razorpay_dispute_guidelines.md")
        if not os.path.exists(file_path):
            print(f"File not found: {file_path}")
            return
            
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        print("Starting ingestion of Razorpay policies...")
        
        # 2. Ingest into object storage and track version
        ingestion_service = KnowledgeIngestionService()
        source = ingestion_service.ingest(
            db=db,
            source_type=GSourceType.RAZORPAY_POLICY,
            title="Razorpay Dispute and Chargeback Guidelines",
            raw_content=content,
            merchant_id=None,
            metadata={"trust_level": "gold"}
        )
        db.commit()
        db.refresh(source)
        print(f"Ingested KnowledgeSource: {source.id} (Version {source.version})")
        
        # 3. Chunk the document
        chunking_service = KnowledgeChunkingService()
        chunks = chunking_service.chunk_source(source)
        print(f"Chunked document into {len(chunks)} sections.")
        
        # 4. Generate embeddings and persist chunks
        embedding_service = KnowledgeEmbeddingService()
        inserted_count = embedding_service.process_chunks(db, chunks)
        db.commit()
        print(f"Successfully embedded and saved {inserted_count} new KnowledgeChunks.")
        
    except Exception as e:
        print(f"Error during ingestion: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()
