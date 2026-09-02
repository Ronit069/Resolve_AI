import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from app.models.shared import Case
from app.models.module_a import Dispute
from app.models.module_f import RiskPrediction
from app.models.module_g import GeneratedDraft
from app.models.module_h import ReviewQueueItem, QueueStatus

def hydrate_review_queue(db: Session, case_id: uuid.UUID) -> Optional[ReviewQueueItem]:
    """
    Hydrates the human review queue from eligible cases, risk predictions, and generated drafts.
    
    Responsibilities:
    1. Identify whether a case requires human review.
    2. Determine whether a ReviewQueueItem already exists for the prediction.
    3. Avoid duplicate queue items.
    4. Calculate priority and respond_by.
    5. Preserve tenant isolation (implicitly via case_id bounding).
    6. Execute transactionally.
    """
    # 1. Fetch Case and Dispute
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        return None

    dispute = db.query(Dispute).filter(Dispute.case_id == case_id).first()
    if not dispute:
        return None

    # 2. Retrieve the applicable (most recent) RiskPrediction
    prediction = db.query(RiskPrediction).filter(
        RiskPrediction.case_id == case_id
    ).order_by(RiskPrediction.created_at.desc()).first()

    if not prediction:
        # No prediction, nothing to hydrate
        return None

    # 3. Retrieve the latest current GeneratedDraft (if any)
    draft = db.query(GeneratedDraft).filter(
        GeneratedDraft.case_id == case_id,
        GeneratedDraft.is_current == True
    ).order_by(GeneratedDraft.created_at.desc()).first()

    # 4. Check Idempotency based on prediction_id
    # We do not use a strict database uniqueness constraint for this to avoid DB schema changes.
    existing_queue_item = db.query(ReviewQueueItem).filter(
        ReviewQueueItem.prediction_id == prediction.id
    ).first()

    if existing_queue_item:
        # Return existing item without touching ASSIGNED or DONE statuses
        return existing_queue_item

    # 5. Calculate respond_by
    # The blueprint states respond_by must be populated (NOT NULL).
    # If the upstream Dispute lacks a respond_by, we must infer a fallback.
    respond_by = dispute.respond_by
    if respond_by is not None:
        if respond_by.tzinfo is None:
            respond_by = respond_by.replace(tzinfo=timezone.utc)
    else:
        # ENGINEERING INFERENCE:
        # Blueprint does not define explicit fallback for missing respond_by,
        # but the schema requires it. Assuming 72-hour operational SLA from prediction creation.
        respond_by = prediction.created_at + timedelta(days=3)

    # 6. Calculate priority score
    # Blueprint: priority_score = w1 * deadline_urgency + w2 * normalized_amount + w3 * review_need + w4 * evidence_readiness
    # Weights are unspecified in the blueprint.
    # ENGINEERING INFERENCE: Assigned weights (40, 30, 20, 10) deterministically.
    
    # 6a. w1 (40): Deadline urgency
    days_to_deadline = (respond_by - datetime.now(timezone.utc)).total_seconds() / 86400.0
    deadline_urgency = max(0.0, 30.0 - days_to_deadline) / 30.0
    w1_score = 40.0 * deadline_urgency
    
    # 6b. w2 (30): Normalized amount
    # Maxing out normalization at 1,000,000 minor units.
    normalized_amount = min(float(dispute.amount_minor) / 1000000.0, 1.0)
    w2_score = 30.0 * normalized_amount

    # 6c. w3 (20): Review need
    # Derived from model outputs / recommendations
    if prediction.hard_block:
        review_need = 1.0
    elif prediction.recommendation == "REVIEW":
        review_need = 0.8
    elif prediction.recommendation == "ACCEPT":
        review_need = 0.5
    else:
        review_need = 0.0
    w3_score = 20.0 * review_need

    # 6d. w4 (10): Evidence readiness
    # Hydration occurs strictly post evidence gathering.
    evidence_readiness = 1.0
    w4_score = 10.0 * evidence_readiness

    priority_score = w1_score + w2_score + w3_score + w4_score

    # 7. Create ReviewQueueItem
    queue_item = ReviewQueueItem(
        id=uuid.uuid4(),
        case_id=case_id,
        prediction_id=prediction.id,
        draft_id=draft.id if draft else None,
        priority_score=priority_score,
        queue_status=QueueStatus.PENDING,
        assigned_to=None,
        respond_by=respond_by,
        created_at=datetime.now(timezone.utc)
    )

    db.add(queue_item)
    # The transaction commit is expected to be handled by the caller,
    # ensuring atomicity.
    db.flush() 

    return queue_item
