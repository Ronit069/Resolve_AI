import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from app.models.module_g import (
    KnowledgeSource,
    KnowledgeChunk,
    RagRetrievalRun,
    RagRetrievedChunk,
    GSourceStatus
)
from app.core.config import settings

class RetrievedChunkDTO(BaseModel):
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    merchant_id: Optional[uuid.UUID]
    content: str
    reason_code: Optional[str]
    source_type: str
    version: int
    similarity_score: float
    rank: int
    metadata: Dict[str, Any]

class KnowledgeRetrievalService:
    def __init__(self, db: Session):
        self.db = db

    def retrieve_knowledge(
        self,
        case_id: uuid.UUID,
        prediction_id: uuid.UUID,
        query_embedding: List[float],
        query_text_redacted: str,
        merchant_id: Optional[uuid.UUID] = None,
        reason_code: Optional[str] = None,
        metadata_filters: Optional[Dict[str, Any]] = None,
        top_k: Optional[int] = None
    ) -> List[RetrievedChunkDTO]:
        
        # 1. Validate vector dimension
        if len(query_embedding) != 1536:
            raise ValueError(f"query_embedding must be exactly 1536 dimensions, got {len(query_embedding)}")

        top_k = top_k or settings.RETRIEVAL_TOP_K
        threshold = settings.RETRIEVAL_SIMILARITY_THRESHOLD
        current_utc = datetime.now(timezone.utc)
        filters_json = metadata_filters or {}
        
        if reason_code:
            filters_json["reason_code"] = reason_code
        if merchant_id:
            filters_json["merchant_id"] = str(merchant_id)

        try:
            # Create base filter for Active/Effective & Tenant Isolation
            base_filter = and_(
                KnowledgeSource.status == GSourceStatus.ACTIVE,
                KnowledgeSource.effective_from <= current_utc,
                or_(
                    KnowledgeSource.effective_to.is_(None),
                    KnowledgeSource.effective_to >= current_utc
                ),
                or_(
                    KnowledgeSource.merchant_id == merchant_id,
                    KnowledgeSource.merchant_id.is_(None)
                )
            )

            # Build metadata JSONB filters if provided
            meta_filters = []
            from sqlalchemy import func
            if metadata_filters:
                for k, v in metadata_filters.items():
                    if k in ["payment_network", "phase", "evidence_type", "source_trust_level"]:
                        meta_filters.append(func.jsonb_extract_path_text(KnowledgeSource.metadata_json, k) == str(v))

            if meta_filters:
                base_filter = and_(base_filter, *meta_filters)

            # Query 1: Exact Reason Code matches
            exact_query = []
            if reason_code:
                exact_query = self.db.query(
                    KnowledgeChunk,
                    KnowledgeSource,
                    KnowledgeChunk.embedding.cosine_distance(query_embedding).label("distance")
                ).join(
                    KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id
                ).filter(
                    base_filter,
                    KnowledgeSource.reason_code == reason_code
                ).all()

            # Query 2: Generic Reason Code matches (reason_code IS NULL)
            generic_query = self.db.query(
                KnowledgeChunk,
                KnowledgeSource,
                KnowledgeChunk.embedding.cosine_distance(query_embedding).label("distance")
            ).join(
                KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id
            ).filter(
                base_filter,
                KnowledgeSource.reason_code.is_(None)
            ).all()

            candidates = []
            
            for chunk, source, dist in exact_query:
                sim_score = round(1.0 - float(dist), 4)
                if sim_score >= threshold:
                    candidates.append({
                        "chunk": chunk,
                        "source": source,
                        "similarity_score": sim_score,
                        "is_exact": True
                    })

            for chunk, source, dist in generic_query:
                sim_score = round(1.0 - float(dist), 4)
                if sim_score >= threshold:
                    candidates.append({
                        "chunk": chunk,
                        "source": source,
                        "similarity_score": sim_score,
                        "is_exact": False
                    })

            # Sort conceptually: exact_reason_code DESC, similarity_score DESC, version DESC, chunk_index ASC
            candidates.sort(
                key=lambda x: (
                    x["is_exact"],
                    x["similarity_score"],
                    x["source"].version,
                    -x["chunk"].chunk_index # negative for ASC in reverse sort
                ),
                reverse=True
            )

            # Take top_k
            final_candidates = candidates[:top_k]

            # Audit Persistence
            retrieval_run = RagRetrievalRun(
                case_id=case_id,
                prediction_id=prediction_id,
                query_text_redacted=query_text_redacted,
                filters_json=filters_json,
                top_k=top_k,
                index_version=None,
                created_at=current_utc
            )
            self.db.add(retrieval_run)
            self.db.flush()

            dtos = []
            for rank_idx, cand in enumerate(final_candidates):
                chunk = cand["chunk"]
                source = cand["source"]
                rank = rank_idx + 1

                chunk_audit = RagRetrievedChunk(
                    retrieval_run_id=retrieval_run.id,
                    chunk_id=chunk.id,
                    rank=rank,
                    similarity_score=cand["similarity_score"],
                    selected_for_prompt=True,
                    selection_reason="Top-K match above threshold"
                )
                self.db.add(chunk_audit)
                
                # Reconstruct provenance metadata
                chunk_meta = dict(chunk.metadata_json) if chunk.metadata_json else {}
                source_meta = dict(source.metadata_json) if source.metadata_json else {}
                
                combined_meta = {**source_meta, **chunk_meta}
                combined_meta["source_id"] = str(source.id)
                combined_meta["source_version"] = source.version
                if source.merchant_id:
                    combined_meta["tenant_id"] = str(source.merchant_id)

                dto = RetrievedChunkDTO(
                    chunk_id=chunk.id,
                    source_id=source.id,
                    merchant_id=source.merchant_id,
                    content=chunk.content,
                    reason_code=source.reason_code,
                    source_type=source.source_type.value,
                    version=source.version,
                    similarity_score=cand["similarity_score"],
                    rank=rank,
                    metadata=combined_meta
                )
                dtos.append(dto)

            self.db.commit()
            return dtos

        except Exception as e:
            self.db.rollback()
            raise e
