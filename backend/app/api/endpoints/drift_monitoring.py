"""
Module L — training/live feature-distribution drift endpoint (issue #11).

Same read-only role-gate convention as model_evaluation.py, a different
surface: PSI-based drift between the champion's training distribution
and the most recently scored cases' live feature snapshots. A missing
training reference returns 503, never a fabricated report; no live
traffic yet is a normal early-deployment state, not an error, so it is
reported as insufficient_data rather than failing the request.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_merchant, require_role
from app.models.shared import Merchant, AppUser, AppUserRole
from app.schemas.module_l import DriftReportResponse
from app.services.observability.drift_monitoring import check_drift, DriftReferenceUnavailable

router = APIRouter()


@router.get("/drift", response_model=DriftReportResponse)
def get_drift_report(
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
    current_user: AppUser = Depends(
        require_role([AppUserRole.MERCHANT_ADMIN, AppUserRole.RISK_ANALYST, AppUserRole.APPROVER])
    ),
):
    """
    Read-only PSI drift comparison between the champion's training
    distribution and the `limit` most recently scored cases. Not
    tenant-scoped -- this is a single global model-health signal, not
    per-merchant data; current_merchant is required only to preserve the
    existing authenticated-request convention used by every other
    observability endpoint.
    """
    try:
        return check_drift(db, limit=limit)
    except DriftReferenceUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
