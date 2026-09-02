import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.api.deps import get_db, get_current_merchant, get_current_user, require_role
from app.models.shared import Merchant, Case, AppUser, AppUserRole
from app.models.module_a import Dispute
from app.models.module_h import ReviewQueueItem, ReviewAction, QueueStatus, ReviewActionEnum
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidenceValidationResult
from app.models.module_f import RiskPrediction, PredictionExplanation
from app.models.module_g import GeneratedDraft, DraftClaim, LLMGuardrailResult
from app.models.module_c import EvidenceDocument
from app.services.review.dual_control import requires_dual_approval

from app.schemas.module_h import (
    CaseWorkspaceResponse, H02CaseSchema, H02DisputeSchema, H02QueueItemSchema,
    H02FeatureSnapshotSchema, H02ValidationFindingsSchema, H02ValidationResultSchema,
    H02RiskPredictionSchema, H02PredictionExplanationSchema,
    H02GeneratedDraftSchema, H02DraftClaimSchema, H02GuardrailResultSchema,
    H02EvidenceDocumentSchema, H02UncertaintyWarningSchema,
    ReviewActionCreateRequest, ReviewActionResponse
)

router = APIRouter()

@router.get("/{case_id}/workspace", response_model=CaseWorkspaceResponse)
def get_case_review_workspace(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant)
):
    # 1-4. Authenticate and enforce tenant isolation on Case
    case = db.query(Case).filter(
        Case.case_id == case_id,
        Case.merchant_id == current_merchant.merchant_id
    ).first()
    
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found"
        )
        
    # 5. Retrieve Dispute
    dispute = db.query(Dispute).filter(Dispute.case_id == case_id).first()
    if not dispute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dispute data not found for case"
        )
        
    # 6. Retrieve ReviewQueueItem (optional)
    queue_item = db.query(ReviewQueueItem).filter(ReviewQueueItem.case_id == case_id).first()
    
    # 7. Retrieve current CaseFeatureSnapshot
    feature_snapshot = db.query(CaseFeatureSnapshot).filter(
        CaseFeatureSnapshot.case_id == case_id,
        CaseFeatureSnapshot.is_current == True
    ).first()
    
    # 8. Retrieve Module E findings
    evidence_findings = None
    val_run = db.query(EvidenceValidationRun).filter(
        EvidenceValidationRun.case_id == case_id
    ).order_by(EvidenceValidationRun.created_at.desc()).first()
    
    if val_run:
        val_results = db.query(EvidenceValidationResult).filter(
            EvidenceValidationResult.validation_run_id == val_run.id
        ).all()
        evidence_findings = H02ValidationFindingsSchema(
            status=val_run.status.value,
            results=[H02ValidationResultSchema.model_validate(r) for r in val_results]
        )
        
    # 9-10. Retrieve latest RiskPrediction and Explanations
    risk_prediction_model = None
    prediction = db.query(RiskPrediction).filter(
        RiskPrediction.case_id == case_id
    ).order_by(RiskPrediction.created_at.desc()).first()
    
    if prediction:
        explanations = db.query(PredictionExplanation).filter(
            PredictionExplanation.prediction_id == prediction.id
        ).order_by(PredictionExplanation.rank).all()
        
        risk_prediction_model = H02RiskPredictionSchema(
            prediction_id=prediction.id,
            calibrated_probability=float(prediction.calibrated_probability),
            recommendation=prediction.recommendation,
            hard_block=prediction.hard_block,
            explanations=[
                H02PredictionExplanationSchema(
                    explanation_id=e.id,
                    prediction_id=e.prediction_id,
                    feature_name=e.feature_name,
                    shap_value=float(e.contribution) if e.contribution is not None else None,
                    display_text=e.display_text
                )
                for e in explanations
            ]
        )
        
    # 11-13. Retrieve current GeneratedDraft, Claims, and Guardrails
    current_draft_model = None
    draft = db.query(GeneratedDraft).filter(
        GeneratedDraft.case_id == case_id,
        GeneratedDraft.is_current == True
    ).first()
    
    if draft:
        claims = db.query(DraftClaim).filter(DraftClaim.draft_id == draft.id).all()
        guardrails = db.query(LLMGuardrailResult).filter(LLMGuardrailResult.draft_id == draft.id).all()
        
        current_draft_model = H02GeneratedDraftSchema(
            id=draft.id,
            summary=draft.summary,
            draft_json=draft.draft_json,
            guardrail_status=draft.guardrail_status.value,
            is_current=draft.is_current,
            claims=[H02DraftClaimSchema.model_validate(c) for c in claims],
            guardrail_results=[H02GuardrailResultSchema.model_validate(g) for g in guardrails]
        )
        
    # 14. Retrieve EvidenceDocuments metadata (no preview URL subsystem)
    evidence_docs = db.query(EvidenceDocument).filter(
        EvidenceDocument.case_id == case_id
    ).all()
    
    # 15. Construct Uncertainty Warnings
    warnings = []
    
    if prediction and prediction.hard_block:
        warnings.append(H02UncertaintyWarningSchema(
            source="MODULE_F",
            type="HARD_BLOCK",
            message="Risk prediction contains a hard block."
        ))
        
    if val_run:
        severe_results = [r for r in val_results if r.severity.value == "ERROR" and r.result.value == "FAIL"]
        for sr in severe_results:
            warnings.append(H02UncertaintyWarningSchema(
                source="MODULE_E",
                type="VALIDATION_FAILURE",
                message=f"Validation failed with severity {sr.severity.value}: {sr.explanation or 'No explanation provided'}"
            ))
            
    if draft:
        if draft.guardrail_status.value != "PASS":
            warnings.append(H02UncertaintyWarningSchema(
                source="MODULE_G",
                type="GUARDRAIL_WARNING",
                message=f"Draft guardrail status is {draft.guardrail_status.value}."
            ))
        for claim in claims:
            if claim.support_status.value != "SUPPORTED":
                warnings.append(H02UncertaintyWarningSchema(
                    source="MODULE_G",
                    type="CLAIM_SUPPORT_WARNING",
                    message=f"Draft claim '{claim.claim_text}' has support status {claim.support_status.value}."
                ))
    
    # 16-17. Construct and return response
    return CaseWorkspaceResponse(
        case=H02CaseSchema.model_validate(case),
        dispute=H02DisputeSchema.model_validate(dispute),
        queue_item=H02QueueItemSchema.model_validate(queue_item) if queue_item else None,
        feature_snapshot=H02FeatureSnapshotSchema.model_validate(feature_snapshot) if feature_snapshot else None,
        evidence_findings=evidence_findings,
        risk_prediction=risk_prediction_model,
        current_draft=current_draft_model,
        evidence_documents=[H02EvidenceDocumentSchema.model_validate(doc) for doc in evidence_docs],
        uncertainty_warnings=warnings
    )

def _needs_h18_override(prediction: RiskPrediction, action: ReviewActionEnum) -> bool:
    """H-18: contradiction/override conditions requiring reason_code + notes."""
    if prediction.recommendation == "ACCEPT" and action == ReviewActionEnum.APPROVE_CONTEST:
        return True
    if prediction.recommendation == "CONTEST" and action == ReviewActionEnum.APPROVE_ACCEPT:
        return True
    if prediction.hard_block and action == ReviewActionEnum.APPROVE_CONTEST:
        return True
    if prediction.recommendation == "REVIEW" and action == ReviewActionEnum.APPROVE_CONTEST:
        return True
    if action == ReviewActionEnum.REJECT_RECOMMENDATION:
        return True
    return False


@router.post("/{case_id}/review-action", response_model=ReviewActionResponse, status_code=status.HTTP_201_CREATED)
def submit_review_action(
    case_id: uuid.UUID,
    request: ReviewActionCreateRequest,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
    current_user: AppUser = Depends(require_role([AppUserRole.APPROVER]))
):
    # 1-3. Verify the Case belongs to current merchant
    case = db.query(Case).filter(
        Case.case_id == case_id,
        Case.merchant_id == current_merchant.merchant_id
    ).first()
    
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found"
        )

    # 4-5. Locate and lock an actionable ReviewQueueItem for the case
    queue_item = db.query(ReviewQueueItem).filter(
        ReviewQueueItem.case_id == case_id
    ).with_for_update().first()

    if not queue_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No actionable queue item found"
        )
        
    # 6. Verify queue state
    if queue_item.queue_status == QueueStatus.DONE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Queue item is already DONE"
        )

    # 7. Load RiskPrediction to enforce H-18 / H-05
    prediction = db.query(RiskPrediction).filter(
        RiskPrediction.id == queue_item.prediction_id
    ).first()

    if not prediction:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Associated risk prediction not found"
        )

    # H-05 Dual Control: case is awaiting a second, distinct APPROVER
    if queue_item.queue_status == QueueStatus.PENDING_SECOND_APPROVAL:
        pending_action = db.query(ReviewAction).filter(
            ReviewAction.id == queue_item.pending_review_action_id
        ).first()

        if not pending_action:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Pending first approval could not be located"
            )

        # ESCALATE is always accepted while pending: it cancels the pending
        # second-approval state without ever finalizing the decision, and may
        # be submitted by any active APPROVER, including the original first approver.
        if request.action == ReviewActionEnum.ESCALATE:
            escalate_action = ReviewAction(
                id=uuid.uuid4(),
                queue_item_id=queue_item.id,
                case_id=case_id,
                reviewer_id=current_user.user_id,
                action=ReviewActionEnum.ESCALATE,
                override_reason_code=request.override_reason_code,
                notes=request.notes,
                draft_revision_json=None,
            )
            db.add(escalate_action)
            queue_item.queue_status = QueueStatus.DONE
            queue_item.pending_review_action_id = None
            db.commit()
            db.refresh(escalate_action)
            escalate_action.dual_approval_status = "ESCALATED_CANCELLED"
            return escalate_action

        # Any other mismatch with the pending action is rejected.
        if request.action != pending_action.action:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Case is pending second approval; only a matching confirmation of the pending action or ESCALATE is accepted"
            )

        # Dual approval requires two distinct active APPROVER users.
        if current_user.user_id == pending_action.reviewer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Second approval must come from a different active APPROVER than the first"
            )

        # Both approval events must be independently auditable: the second
        # approver's own H-18 justification is required, not inherited from the first.
        if _needs_h18_override(prediction, request.action):
            if not request.override_reason_code or not request.notes:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="override_reason_code and notes are required when contradicting ML recommendation or overriding a hard block"
                )

        second_action = ReviewAction(
            id=uuid.uuid4(),
            queue_item_id=queue_item.id,
            case_id=case_id,
            reviewer_id=current_user.user_id,
            action=request.action,
            override_reason_code=request.override_reason_code,
            notes=request.notes,
            draft_revision_json=request.draft_revision_json if request.action == ReviewActionEnum.EDIT_DRAFT else None,
        )
        db.add(second_action)
        queue_item.queue_status = QueueStatus.DONE
        queue_item.pending_review_action_id = None
        db.commit()
        db.refresh(second_action)
        second_action.dual_approval_status = "FINALIZED"
        return second_action

    # 8-9. Validate H-18 overrides
    needs_override = _needs_h18_override(prediction, request.action)

    if needs_override:
        if not request.override_reason_code or not request.notes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="override_reason_code and notes are required when contradicting ML recommendation or overriding a hard block"
            )

    # 10. Create ReviewAction
    review_action = ReviewAction(
        id=uuid.uuid4(),
        queue_item_id=queue_item.id,
        case_id=case_id,
        reviewer_id=current_user.user_id,
        action=request.action,
        override_reason_code=request.override_reason_code,
        notes=request.notes,
        draft_revision_json=request.draft_revision_json if request.action == ReviewActionEnum.EDIT_DRAFT else None,
    )

    db.add(review_action)

    # H-05 Dual Control: gate APPROVE_CONTEST/APPROVE_ACCEPT on high amount or hard-block override.
    # Fail closed if the dispute cannot be loaded: without it we cannot verify the
    # amount-based threshold, so we must not silently finalize a gated decision.
    if request.action in (ReviewActionEnum.APPROVE_CONTEST, ReviewActionEnum.APPROVE_ACCEPT):
        dispute = db.query(Dispute).filter(Dispute.case_id == case_id).first()
        if dispute is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Associated dispute not found; cannot evaluate H-05 dual control"
            )
        if requires_dual_approval(request.action, prediction, dispute):
            db.flush()  # assign review_action.id before referencing it
            queue_item.pending_review_action_id = review_action.id
            queue_item.queue_status = QueueStatus.PENDING_SECOND_APPROVAL
            db.commit()
            db.refresh(review_action)
            review_action.dual_approval_status = "AWAITING_SECOND_APPROVAL"
            return review_action

    # 11. Update queue status
    queue_item.queue_status = QueueStatus.DONE

    # 12. Flush/commit transaction
    db.commit()
    db.refresh(review_action)

    # 13. Return created ReviewAction
    review_action.dual_approval_status = None
    return review_action
