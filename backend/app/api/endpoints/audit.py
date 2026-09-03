import dataclasses
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_merchant, require_role
from app.models.shared import Merchant, AppUser, AppUserRole, Case
from app.schemas.module_i import I07AuditFeedResponse, I07ActivityEventSchema
from app.services.audit.audit_feed import get_case_activity_feed, DEFAULT_LIMIT

router = APIRouter()


@router.get("/{case_id}/audit-log", response_model=I07AuditFeedResponse)
def get_case_audit_log(
    case_id: uuid.UUID,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
    current_user: AppUser = Depends(
        require_role([AppUserRole.MERCHANT_ADMIN, AppUserRole.RISK_ANALYST, AppUserRole.APPROVER])
    ),
):
    """I-07 Audit/Activity: read-only, deterministic merge of AuditLog + ReviewAction."""
    case = db.query(Case).filter(
        Case.case_id == case_id,
        Case.merchant_id == current_merchant.merchant_id,
    ).first()
    if not case:
        # Cross-tenant access is deliberately indistinguishable from
        # not-found, matching the existing anti-enumeration convention
        # used by review.py's workspace/review-action endpoints.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    result = get_case_activity_feed(db, case_id, limit=limit, offset=offset)
    return I07AuditFeedResponse(
        items=[I07ActivityEventSchema(**dataclasses.asdict(e)) for e in result.items],
        total_count=result.total_count,
        limit=result.limit,
        offset=result.offset,
    )
