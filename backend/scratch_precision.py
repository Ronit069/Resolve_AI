import sys
sys.path.insert(0, "C:/Users/ronit/Desktop/Resolve_AI/backend")
from app.core.database import SessionLocal
from app.models.module_g import KnowledgeChunk, KnowledgeSource
from sqlalchemy import text

db = SessionLocal()

s = db.query(KnowledgeSource).filter_by(title='Threshold Source').first()
chunks = db.query(KnowledgeChunk).filter_by(source_id=s.id).order_by(KnowledgeChunk.chunk_index).all()
query_vec = [0.0]*1536
query_vec[0] = 1.0

for c in chunks:
    dist = db.execute(text("SELECT embedding <=> :q FROM knowledge_chunks WHERE id = :id"), {"q": str(query_vec), "id": c.id}).scalar()
    print(f"Chunk {c.chunk_index} ({c.content}): distance={dist}, sim_score={1.0-dist}")
