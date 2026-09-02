from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_merchant, require_role
from app.models.shared import Merchant, AppUser, AppUserRole
from app.schemas.module_h import (
    H22ObservabilityMetricsResponse,
    H22QueueAgeMetrics,
    H22NearDeadlineMetrics,
    H22ReviewTurnaroundMetrics,
)
from app.services.observability.queue_metrics import (
    compute_queue_age_metrics,
    compute_near_deadline_metrics,
    compute_review_turnaround_metrics,
)

router = APIRouter()


@router.get("/queue-metrics", response_model=H22ObservabilityMetricsResponse)
def get_queue_metrics(
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
    current_user: AppUser = Depends(
        require_role([AppUserRole.MERCHANT_ADMIN, AppUserRole.RISK_ANALYST, AppUserRole.APPROVER])
    ),
):
    """H-22 Observability: queue age, cases near deadline, review turnaround. Read-only."""
    now = datetime.now(timezone.utc)

    age = compute_queue_age_metrics(db, current_merchant.merchant_id, now)
    deadline = compute_near_deadline_metrics(db, current_merchant.merchant_id, now)
    turnaround = compute_review_turnaround_metrics(db, current_merchant.merchant_id, now)

    return H22ObservabilityMetricsResponse(
        generated_at=now,
        queue_age=H22QueueAgeMetrics(
            active_item_count=age.active_item_count,
            average_age_seconds=age.average_age_seconds,
            min_age_seconds=age.min_age_seconds,
            max_age_seconds=age.max_age_seconds,
        ),
        near_deadline=H22NearDeadlineMetrics(
            threshold_hours=deadline.threshold_hours,
            near_deadline_count=deadline.near_deadline_count,
            expired_count=deadline.expired_count,
        ),
        review_turnaround=H22ReviewTurnaroundMetrics(
            completed_item_count=turnaround.completed_item_count,
            average_turnaround_seconds=turnaround.average_turnaround_seconds,
            min_turnaround_seconds=turnaround.min_turnaround_seconds,
            max_turnaround_seconds=turnaround.max_turnaround_seconds,
        ),
    )
