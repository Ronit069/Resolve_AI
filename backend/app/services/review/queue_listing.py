"""
Module I — I-03 Dispute Queue listing.

Read-only aggregate/listing query over already-populated, frozen Module A/H
tables. No writes, no new persistence, no business rule beyond ordering and
pagination. Mirrors the pure-function, merchant-scoped pattern established by
app/services/observability/queue_metrics.py (H-22).
"""

import dataclasses
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.shared import Case
from app.models.module_a import Dispute
from app.models.module_h import ReviewQueueItem, QueueStatus
from app.models.module_f import RiskPrediction

SORT_MODES = {
    "respond_by:asc": (ReviewQueueItem.respond_by.asc(), ReviewQueueItem.id.asc()),
    "priority_score:desc": (ReviewQueueItem.priority_score.desc(), ReviewQueueItem.id.asc()),
}
DEFAULT_SORT = "respond_by:asc"
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


@dataclasses.dataclass(frozen=True)
class QueueListingItem:
    case_id: UUID
    queue_item_id: UUID
    queue_status: str
    priority_score: float
    respond_by: object
    dispute_amount_minor: int
    dispute_currency: str
    dispute_reason_code: str
    dispute_status: str
    recommendation: Optional[str]
    hard_block: Optional[bool]


@dataclasses.dataclass(frozen=True)
class QueueListingResult:
    items: List[QueueListingItem]
    total_count: int
    limit: int
    offset: int


def _latest_prediction_by_case(db: Session, case_ids: List[UUID]):
    if not case_ids:
        return {}
    rows = (
        db.query(RiskPrediction)
        .filter(RiskPrediction.case_id.in_(case_ids))
        .order_by(RiskPrediction.case_id, RiskPrediction.created_at.desc())
        .all()
    )
    latest = {}
    for row in rows:
        if row.case_id not in latest:
            latest[row.case_id] = row
    return latest


def list_review_queue(
    db: Session,
    merchant_id: UUID,
    status: Optional[QueueStatus] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    sort: str = DEFAULT_SORT,
) -> QueueListingResult:
    """
    Read-only, merchant-scoped listing of ReviewQueueItem rows joined with
    Case/Dispute, plus each case's most recent RiskPrediction. No writes.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    order_by = SORT_MODES.get(sort, SORT_MODES[DEFAULT_SORT])

    base_query = (
        db.query(ReviewQueueItem, Case, Dispute)
        .join(Case, Case.case_id == ReviewQueueItem.case_id)
        .join(Dispute, Dispute.case_id == Case.case_id)
        .filter(Case.merchant_id == merchant_id)
    )
    if status is not None:
        base_query = base_query.filter(ReviewQueueItem.queue_status == status)

    total_count = base_query.with_entities(func.count(ReviewQueueItem.id)).scalar() or 0

    rows = (
        base_query.order_by(*order_by)
        .offset(offset)
        .limit(limit)
        .all()
    )

    case_ids = [case.case_id for _, case, _ in rows]
    predictions_by_case = _latest_prediction_by_case(db, case_ids)

    items = []
    for queue_item, case, dispute in rows:
        prediction = predictions_by_case.get(case.case_id)
        items.append(
            QueueListingItem(
                case_id=case.case_id,
                queue_item_id=queue_item.id,
                queue_status=queue_item.queue_status.value,
                priority_score=float(queue_item.priority_score),
                respond_by=queue_item.respond_by,
                dispute_amount_minor=dispute.amount_minor,
                dispute_currency=dispute.currency,
                dispute_reason_code=dispute.reason_code,
                dispute_status=dispute.status,
                recommendation=prediction.recommendation if prediction else None,
                hard_block=prediction.hard_block if prediction else None,
            )
        )

    return QueueListingResult(items=items, total_count=total_count, limit=limit, offset=offset)
