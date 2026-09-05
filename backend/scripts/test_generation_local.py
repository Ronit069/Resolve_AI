import os
import sys
import uuid
sys.path.append(os.path.abspath("."))

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
from app.models.shared import Case, Merchant
from app.models.module_f import RiskPrediction
from app.models.module_e import CaseFeatureSnapshot
from app.services.rag.generation import LLMGenerationService
from app.services.rag.context import ContextAssembler
from app.services.rag.retrieval import KnowledgeRetrievalService
from app.core.config import settings
import openai

def main():
    db = SessionLocal()
    try:
        case_id = uuid.UUID("0f28fa8b-bc34-4749-a66b-70854bb7440d")
        case = db.query(Case).filter(Case.case_id == case_id).first()
        prediction = db.query(RiskPrediction).filter(RiskPrediction.case_id == case_id).order_by(RiskPrediction.created_at.desc()).first()
        snapshot = db.query(CaseFeatureSnapshot).filter(CaseFeatureSnapshot.id == prediction.feature_snapshot_id).first()
        
        # Manually run retrieval
        retrieval_service = KnowledgeRetrievalService(db)
        
        # Mock embedding since we want to see it work
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        # Create a mock query embedding of size 1536
        query_text = dict(snapshot.features_json).get("dispute_reason_code", "fraud")
        
        # Override threshold to ensure we get chunks
        settings.RETRIEVAL_SIMILARITY_THRESHOLD = 0.0
        
        print(f"Generating embedding for retrieval with query: {query_text}...")
        response = client.embeddings.create(input=[query_text], model="text-embedding-3-small")
        query_embedding = response.data[0].embedding
        
        print("Retrieving chunks...")
        retrieved_chunks = retrieval_service.retrieve_knowledge(
            case_id=case_id,
            prediction_id=prediction.id,
            query_embedding=query_embedding,
            query_text_redacted=query_text,
            merchant_id=case.merchant_id
        )
        print(f"Retrieved {len(retrieved_chunks)} chunks.")
        
        # Assembly
        assembler = ContextAssembler()
        packet = assembler.assemble(
            case_id=case_id,
            merchant_id=case.merchant_id,
            case_merchant_id=case.merchant_id,
            prediction_id=prediction.id,
            feature_snapshot_id=snapshot.id,
            features_json=dict(snapshot.features_json),
            recommendation=prediction.recommendation,
            calibrated_probability=float(prediction.calibrated_probability),
            hard_block=prediction.hard_block,
            retrieved_chunks=retrieved_chunks,
            retrieval_run_id=None
        )
        
        # Generation
        print("Generating draft...")
        service = LLMGenerationService(db)
        # Will print the LLM exception if it fails because of our print statement
        result = service.generate_draft(
            packet=packet,
            case_id=case_id,
            prediction_id=prediction.id
        )
        
        print(result.model_dump_json(indent=2))
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
