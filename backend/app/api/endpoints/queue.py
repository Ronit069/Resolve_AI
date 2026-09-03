import dataclasses
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_merchant, require_role
from app.models.shared import Merchant, AppUser, AppUserRole
from app.models.module_h import QueueStatus
from app.schemas.module_i import I03QueueListingResponse, I03QueueItemSchema
from app.services.review.queue_listing import list_review_queue, DEFAULT_SORT, DEFAULT_LIMIT

router = APIRouter()


@router.get("/queue", response_model=I03QueueListingResponse)
def get_review_queue(
    status: Optional[QueueStatus] = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default=DEFAULT_SORT),
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
    current_user: AppUser = Depends(
        require_role([AppUserRole.MERCHANT_ADMIN, AppUserRole.RISK_ANALYST, AppUserRole.APPROVER])
    ),
):
    """I-03 Dispute Queue: read-only, paginated, merchant-scoped listing."""
    result = list_review_queue(
        db, current_merchant.merchant_id, status=status, limit=limit, offset=offset, sort=sort
    )
    return I03QueueListingResponse(
        items=[I03QueueItemSchema(**dataclasses.asdict(item)) for item in result.items],
        total_count=result.total_count,
        limit=result.limit,
        offset=result.offset,
    )
