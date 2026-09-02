import uuid
from typing import List, Dict, Any, Union
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.module_g import (
    RagEvaluationQuery,
    RagEvaluationRun,
    LLMGuardrailResult,
    GuardrailCheckType,
    DraftClaim,
    SupportStatus
)

class RagEvaluationService:
    def __init__(self, db: Session):
        self.db = db

    def evaluate_retrieval(self, query: RagEvaluationQuery, retrieved_chunk_ids: List[Union[str, uuid.UUID]], k: int = 3) -> RagEvaluationRun:
        """
        Evaluate a single gold retrieval query against actual retrieved chunks.
        [BLUEPRINT REQUIREMENT] G-19: retrieval hit rate and precision@k
        """
        if not retrieved_chunk_ids:
            hit = 0.0
            precision = 0.0
        else:
            top_k_retrieved = retrieved_chunk_ids[:k]
            # Expected chunks list of UUIDs
            expected = set(uuid.UUID(str(c)) for c in query.expected_chunk_ids)
            retrieved_set = set(uuid.UUID(str(c)) for c in top_k_retrieved)
            
            intersection_count = len(expected.intersection(retrieved_set))
            
            # Hit rate: 1 if at least one expected chunk appears in top_k
            hit = 1.0 if intersection_count > 0 else 0.0
            
            # Precision@k: relevant retrieved / k
            precision = float(intersection_count) / float(k)
        
        run = RagEvaluationRun(
            k_value=k,
            hit_rate=hit,
            precision_at_k=precision
        )
        self.db.add(run)
        self.db.flush()
        return run

    def get_aggregate_groundedness(self) -> Dict[str, Any]:
        """
        [BLUEPRINT REQUIREMENT] G-19: Draft groundedness aggregate
        Uses existing LLMGuardrailResult GROUNDING results.
        Numerator: GROUNDING checks with result == 'PASS'
        Denominator: Total GROUNDING checks
        """
        total = self.db.query(func.count(LLMGuardrailResult.id)).filter(
            LLMGuardrailResult.check_type == GuardrailCheckType.GROUNDING
        ).scalar() or 0
        
        if total == 0:
            return {"metric": "groundedness", "rate_percentage": None, "numerator": 0, "denominator": 0}
            
        passes = self.db.query(func.count(LLMGuardrailResult.id)).filter(
            LLMGuardrailResult.check_type == GuardrailCheckType.GROUNDING,
            LLMGuardrailResult.result == "PASS"
        ).scalar() or 0
        
        return {
            "metric": "groundedness",
            "rate_percentage": (float(passes) / float(total)) * 100.0,
            "numerator": passes,
            "denominator": total
        }

    def get_aggregate_claim_support(self) -> Dict[str, Any]:
        """
        [BLUEPRINT REQUIREMENT] G-19: Claim support rate aggregate
        Uses existing DraftClaim.support_status.
        Numerator: Claims with support_status == 'SUPPORTED'
        Denominator: Total claims
        """
        total = self.db.query(func.count(DraftClaim.id)).scalar() or 0
        
        if total == 0:
            return {"metric": "claim_support", "rate_percentage": None, "numerator": 0, "denominator": 0}
            
        supported = self.db.query(func.count(DraftClaim.id)).filter(
            DraftClaim.support_status == SupportStatus.SUPPORTED
        ).scalar() or 0
        
        return {
            "metric": "claim_support",
            "rate_percentage": (float(supported) / float(total)) * 100.0,
            "numerator": supported,
            "denominator": total
        }

    def get_aggregate_contradiction_rate(self) -> Dict[str, Any]:
        """
        [BLUEPRINT REQUIREMENT] G-19: Contradiction rate aggregate
        Uses existing LLMGuardrailResult CONTRADICTION results.
        Numerator: CONTRADICTION checks with result == 'FAIL' (meaning a contradiction was found)
        Denominator: Total CONTRADICTION checks
        """
        total = self.db.query(func.count(LLMGuardrailResult.id)).filter(
            LLMGuardrailResult.check_type == GuardrailCheckType.CONTRADICTION
        ).scalar() or 0
        
        if total == 0:
            return {"metric": "contradiction_rate", "rate_percentage": None, "numerator": 0, "denominator": 0}
            
        failures = self.db.query(func.count(LLMGuardrailResult.id)).filter(
            LLMGuardrailResult.check_type == GuardrailCheckType.CONTRADICTION,
            LLMGuardrailResult.result == "FAIL"
        ).scalar() or 0
        
        return {
            "metric": "contradiction_rate",
            "rate_percentage": (float(failures) / float(total)) * 100.0,
            "numerator": failures,
            "denominator": total
        }

    def get_human_edit_rate(self) -> Dict[str, Any]:
        """
        [BLUEPRINT REQUIREMENT] G-19: Human edit rate.
        DEFERRED — blocked on Module H review_actions implementation.
        """
        return {
            "metric": "human_edit_rate",
            "status": "NOT_AVAILABLE",
            "reason": "DEFERRED: blocked on Module H review_actions implementation",
            "rate_percentage": None
        }
