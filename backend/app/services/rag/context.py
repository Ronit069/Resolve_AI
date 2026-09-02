"""
G-05: Fact Packet / Context Assembly
[BLUEPRINT REQUIREMENT] G-07: Fact allowlist -- LLM case facts assembled from
Module B trusted facts + Module E verified fields + Module F recommendation.
[BLUEPRINT REQUIREMENT] G-15: Prompt-injection resistance -- retrieved content
is DATA, never instructions.
"""
import hashlib
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from app.services.rag.retrieval import RetrievedChunkDTO


# ---------------------------------------------------------------------------
# Allowlisted fact fields
# [BLUEPRINT REQUIREMENT] G-07: only allowlisted verified fields enter prompt.
# ---------------------------------------------------------------------------

VERIFIED_FACT_ALLOWLIST = {
    "payment_id", "payment_amount_minor", "payment_currency", "payment_status",
    "payment_method", "payment_captured_at",
    "order_id", "order_amount_minor", "order_status",
    "dispute_reason_code", "dispute_amount_minor", "dispute_currency",
    "dispute_phase", "dispute_deadline_at",
    "refund_total_minor", "refund_count",
    "shipment_tracking_id", "delivery_status", "delivery_date",
    "required_evidence_coverage", "identifier_match_rate",
    "timeline_consistency_score", "unknown_field_ratio", "contradiction_count",
    "recommendation", "calibrated_probability", "hard_block",
}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class PolicyEvidence(BaseModel):
    """A retrieved knowledge chunk -- strictly labelled as DATA."""
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    rank: int
    similarity_score: float
    reason_code: Optional[str]
    source_type: str
    version: int
    content_data: str   # policy text -- DATA, never executable instructions
    metadata: Dict[str, Any]

    model_config = {"json_encoders": {uuid.UUID: str}}


class VerifiedFacts(BaseModel):
    """Allowlisted, structured case facts from CaseFeatureSnapshot + RiskPrediction."""
    case_id: uuid.UUID
    prediction_id: uuid.UUID
    feature_snapshot_id: uuid.UUID
    facts: Dict[str, Any]

    model_config = {"json_encoders": {uuid.UUID: str}}


class DecisionContext(BaseModel):
    """Module F decision context -- read-only by G-05."""
    recommendation: str
    calibrated_probability: float
    hard_block: bool


class FactPacket(BaseModel):
    """
    Full input packet for G-05 LLM generation.
    [BLUEPRINT REQUIREMENT] G-07: strict namespace separation.
    """
    verified_facts: VerifiedFacts
    policy_evidence: List[PolicyEvidence]
    decision_context: DecisionContext
    intended_action: str
    retrieval_run_id: Optional[uuid.UUID]

    model_config = {"json_encoders": {uuid.UUID: str}}


# ---------------------------------------------------------------------------
# Context Assembler
# ---------------------------------------------------------------------------

class ContextAssemblyError(Exception):
    """Raised when the fact packet cannot be assembled safely."""


class ContextAssembler:
    """
    Builds a FactPacket from authoritative upstream data.
    [BLUEPRINT REQUIREMENT] G-07 + G-15.
    """

    def assemble(
        self,
        *,
        case_id: uuid.UUID,
        merchant_id: uuid.UUID,
        prediction_id: uuid.UUID,
        feature_snapshot_id: uuid.UUID,
        features_json: Dict[str, Any],
        recommendation: str,
        calibrated_probability: float,
        hard_block: bool,
        retrieved_chunks: List[RetrievedChunkDTO],
        retrieval_run_id: Optional[uuid.UUID] = None,
        case_merchant_id: uuid.UUID,
    ) -> FactPacket:
        # [BLUEPRINT REQUIREMENT] Tenant isolation -- fail closed
        if case_merchant_id != merchant_id:
            raise ContextAssemblyError(
                f"Tenant mismatch: case merchant {case_merchant_id} != caller {merchant_id}"
            )

        # [BLUEPRINT REQUIREMENT] G-07: strip disallowed fields
        allowed_facts: Dict[str, Any] = {
            k: v for k, v in features_json.items()
            if k in VERIFIED_FACT_ALLOWLIST
        }
        allowed_facts["recommendation"] = recommendation
        allowed_facts["calibrated_probability"] = float(calibrated_probability)
        allowed_facts["hard_block"] = hard_block

        verified = VerifiedFacts(
            case_id=case_id,
            prediction_id=prediction_id,
            feature_snapshot_id=feature_snapshot_id,
            facts=allowed_facts,
        )

        # [BLUEPRINT REQUIREMENT] G-15: label content as DATA
        evidence: List[PolicyEvidence] = []
        for dto in retrieved_chunks:
            evidence.append(PolicyEvidence(
                chunk_id=dto.chunk_id,
                source_id=dto.source_id,
                rank=dto.rank,
                similarity_score=dto.similarity_score,
                reason_code=dto.reason_code,
                source_type=dto.source_type,
                version=dto.version,
                content_data=dto.content,
                metadata=dto.metadata,
            ))

        return FactPacket(
            verified_facts=verified,
            policy_evidence=evidence,
            decision_context=DecisionContext(
                recommendation=recommendation,
                calibrated_probability=float(calibrated_probability),
                hard_block=hard_block,
            ),
            intended_action="draft_contest_response",
            retrieval_run_id=retrieval_run_id,
        )


def compute_fact_packet_hash(packet: FactPacket) -> str:
    """Deterministic SHA-256 of the fact packet for audit (G-20)."""
    payload = packet.model_dump_json(exclude_none=True)
    return hashlib.sha256(payload.encode()).hexdigest()
