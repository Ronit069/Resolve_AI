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

# One document per dispute category, each its own KnowledgeSource so
# retrieval.py's exact-reason_code-match path (which prioritizes a
# source tagged with the dispute's actual reason_code over generic,
# reason_code-less sources) has real, specific content to prefer --
# previously every category was folded into a single reason_code=None
# document, so that prioritization logic was never actually exercised.
#
# reason_code values are the commonly-published Visa dispute category
# codes for each scenario; always confirm the exact code shown on the
# Razorpay dashboard for a given dispute, since code numbering differs
# by network and has been revised by network rule changes over time.
# "overview" and "processing_error" are left reason_code=None since
# they are genuinely cross-cutting / span multiple underlying codes.
DOCUMENTS = [
    {
        "file": "overview_and_merchant_guidelines.md",
        "title": "Razorpay Dispute Process Overview and Merchant Guidelines",
        "reason_code": None,
        "metadata": {"trust_level": "gold", "category": "overview"},
    },
    {
        "file": "fraud_unauthorized_transaction.md",
        "title": "Fraud or Unauthorized Transaction -- Evidence Guidance",
        "reason_code": "10.4",
        "metadata": {"trust_level": "gold", "payment_network": "Visa", "category": "fraud"},
    },
    {
        "file": "non_receipt_of_goods.md",
        "title": "Non-Receipt of Goods or Services -- Evidence Guidance",
        "reason_code": "13.1",
        "metadata": {"trust_level": "gold", "payment_network": "Visa", "category": "non_receipt"},
    },
    {
        "file": "not_as_described.md",
        "title": "Services or Merchandise Not as Described -- Evidence Guidance",
        "reason_code": "13.3",
        "metadata": {"trust_level": "gold", "payment_network": "Visa", "category": "not_as_described"},
    },
    {
        "file": "credit_not_processed.md",
        "title": "Credit Not Processed -- Evidence Guidance",
        "reason_code": "13.6",
        "metadata": {"trust_level": "gold", "payment_network": "Visa", "category": "credit_not_processed"},
    },
    {
        "file": "processing_errors.md",
        "title": "Processing Errors -- Evidence Guidance",
        "reason_code": None,
        "metadata": {"trust_level": "gold", "payment_network": "Visa", "category": "processing_error"},
    },
]


def main():
    db = SessionLocal()

    ingestion_service = KnowledgeIngestionService()
    chunking_service = KnowledgeChunkingService()
    embedding_service = KnowledgeEmbeddingService()

    data_dir = os.path.join(os.path.dirname(__file__), "data", "knowledge")
    total_sources = 0
    total_chunks = 0

    try:
        for doc in DOCUMENTS:
            file_path = os.path.join(data_dir, doc["file"])
            if not os.path.exists(file_path):
                print(f"File not found, skipping: {file_path}")
                continue

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            print(f"Ingesting {doc['file']} (reason_code={doc['reason_code']})...")

            source = ingestion_service.ingest(
                db=db,
                source_type=GSourceType.RAZORPAY_POLICY,
                title=doc["title"],
                raw_content=content,
                reason_code=doc["reason_code"],
                merchant_id=None,
                metadata=doc["metadata"],
            )
            db.commit()
            db.refresh(source)
            print(f"  KnowledgeSource: {source.id} (version {source.version})")

            chunks = chunking_service.chunk_source(source)
            print(f"  Chunked into {len(chunks)} section(s).")

            inserted_count = embedding_service.process_chunks(db, chunks)
            db.commit()
            print(f"  Embedded and saved {inserted_count} new KnowledgeChunk(s).")

            total_sources += 1
            total_chunks += inserted_count

        print(f"Done: {total_sources} source(s), {total_chunks} new chunk(s) embedded.")

    except Exception as e:
        print(f"Error during ingestion: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
