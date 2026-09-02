"""
G-05 API Endpoints
[BLUEPRINT REQUIREMENT] POST /api/v1/cases/{id}/generate-draft
[BLUEPRINT REQUIREMENT] GET  /api/v1/cases/{id}/draft
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_merchant
from app.core.database import get_db
from app.models.shared import Case, Merchant
from app.models.module_e import CaseFeatureSnapshot
from app.models.module_f import RiskPrediction
from app.models.module_g import (
    GeneratedDraft,
    RagRetrievalRun,
    RagRetrievedChunk,
)
from app.services.rag.context import ContextAssembler, ContextAssemblyError
from app.services.rag.generation import LLMGenerationService
from app.services.rag.retrieval import RetrievedChunkDTO

router = APIRouter()


class GenerateDraftRequest(BaseModel):
    prediction_id: str
    retrieval_run_id: Optional[str] = None


# ---------------------------------------------------------------------------
# POST /api/v1/cases/{case_id}/generate-draft
# ---------------------------------------------------------------------------

@router.post("/{case_id}/generate-draft", status_code=status.HTTP_201_CREATED)
def generate_draft(
    case_id: str,
    req: GenerateDraftRequest,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    Run context assembly + LLM generation for a case.
    [BLUEPRINT REQUIREMENT] G-18: produces a DRAFT only; no submission.
    """
    # 1. Resolve case + tenant isolation
    try:
        case_uuid = uuid.UUID(case_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid case_id UUID")

    case = db.query(Case).filter(Case.case_id == case_uuid).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case.merchant_id != current_merchant.merchant_id:
        raise HTTPException(status_code=403, detail="Access denied: cross-tenant")

    # 2. Resolve prediction
    try:
        pred_uuid = uuid.UUID(req.prediction_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid prediction_id UUID")

    prediction = db.query(RiskPrediction).filter(
        RiskPrediction.id == pred_uuid,
        RiskPrediction.case_id == case_uuid,
    ).first()
    if not prediction:
        raise HTTPException(status_code=404, detail="Prediction not found for this case")

    # 3. Resolve feature snapshot
    snapshot = db.query(CaseFeatureSnapshot).filter(
        CaseFeatureSnapshot.id == prediction.feature_snapshot_id,
        CaseFeatureSnapshot.case_id == case_uuid,
    ).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Feature snapshot not found")

    # 4. Resolve retrieved chunks (if retrieval_run_id provided)
    retrieved_chunks: list[RetrievedChunkDTO] = []
    retrieval_run_id: Optional[uuid.UUID] = None

    if req.retrieval_run_id:
        try:
            retrieval_run_id = uuid.UUID(req.retrieval_run_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid retrieval_run_id UUID")

        retrieval_run = db.query(RagRetrievalRun).filter(
            RagRetrievalRun.id == retrieval_run_id,
            RagRetrievalRun.case_id == case_uuid,
        ).first()
        if not retrieval_run:
            raise HTTPException(
                status_code=404, detail="Retrieval run not found for this case"
            )

        # Load audited retrieved chunks with provenance
        retrieved_rows = (
            db.query(RagRetrievedChunk)
            .filter(RagRetrievedChunk.retrieval_run_id == retrieval_run_id)
            .order_by(RagRetrievedChunk.rank)
            .all()
        )
        from app.models.module_g import KnowledgeChunk, KnowledgeSource
        for row in retrieved_rows:
            chunk = db.query(KnowledgeChunk).filter(KnowledgeChunk.id == row.chunk_id).first()
            if not chunk:
                continue
            source = db.query(KnowledgeSource).filter(KnowledgeSource.id == chunk.source_id).first()
            if not source:
                continue
            chunk_meta = dict(chunk.metadata_json) if chunk.metadata_json else {}
            source_meta = dict(source.metadata_json) if source.metadata_json else {}
            combined_meta = {**source_meta, **chunk_meta}
            combined_meta["source_id"] = str(source.id)
            combined_meta["source_version"] = source.version

            retrieved_chunks.append(RetrievedChunkDTO(
                chunk_id=chunk.id,
                source_id=source.id,
                merchant_id=source.merchant_id,
                content=chunk.content,
                reason_code=source.reason_code,
                source_type=source.source_type.value,
                version=source.version,
                similarity_score=float(row.similarity_score),
                rank=row.rank,
                metadata=combined_meta,
            ))

    # 5. Assemble fact packet
    assembler = ContextAssembler()
    try:
        packet = assembler.assemble(
            case_id=case_uuid,
            merchant_id=current_merchant.merchant_id,
            prediction_id=prediction.id,
            feature_snapshot_id=snapshot.id,
            features_json=dict(snapshot.features_json),
            recommendation=prediction.recommendation,
            calibrated_probability=float(prediction.calibrated_probability),
            hard_block=prediction.hard_block,
            retrieved_chunks=retrieved_chunks,
            retrieval_run_id=retrieval_run_id,
            case_merchant_id=case.merchant_id,
        )
    except ContextAssemblyError as e:
        raise HTTPException(status_code=403, detail=str(e))

    # 6. Generate draft
    service = LLMGenerationService(db)
    draft_dto = service.generate_draft(
        packet=packet,
        case_id=case_uuid,
        prediction_id=prediction.id,
    )

    return draft_dto.model_dump()


# ---------------------------------------------------------------------------
# GET /api/v1/cases/{case_id}/draft
# [BLUEPRINT REQUIREMENT] blueprint section 5.3
# ---------------------------------------------------------------------------

@router.get("/{case_id}/draft")
def get_current_draft(
    case_id: str,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """Retrieve the current (is_current=True) draft for a case."""
    try:
        case_uuid = uuid.UUID(case_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid case_id UUID")

    case = db.query(Case).filter(Case.case_id == case_uuid).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case.merchant_id != current_merchant.merchant_id:
        raise HTTPException(status_code=403, detail="Access denied: cross-tenant")

    draft = (
        db.query(GeneratedDraft)
        .filter(
            GeneratedDraft.case_id == case_uuid,
            GeneratedDraft.is_current == True,
        )
        .order_by(GeneratedDraft.created_at.desc())
        .first()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="No draft found for this case")

    return {
        "draft_id": str(draft.id),
        "case_id": str(draft.case_id),
        "guardrail_status": draft.guardrail_status.value,
        "summary": draft.summary,
        "contest_amount_minor": str(draft.contest_amount_minor) if draft.contest_amount_minor else None,
        "draft_json": draft.draft_json,
        "created_at": draft.created_at.isoformat(),
    }
