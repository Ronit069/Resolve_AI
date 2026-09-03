"""
Module L, L-04 — runtime/infra observability endpoint.

Deliberately a separate router/file from H-22's queue-metrics endpoint
(app/api/endpoints/observability.py) — same read-only role-gate
convention, different (and previously unowned) metrics surface. Never
merged with H-22 per the frozen Module L non-goal.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_merchant, require_role
from app.models.shared import Merchant, AppUser, AppUserRole
from app.schemas.module_l import RuntimeMetricCategory, RuntimeMetricsResponse
from app.services.observability.runtime_metrics import get_runtime_metrics_summary

router = APIRouter()


@router.get("/runtime-metrics", response_model=RuntimeMetricsResponse)
def get_runtime_metrics(
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
    current_user: AppUser = Depends(
        require_role([AppUserRole.MERCHANT_ADMIN, AppUserRole.RISK_ANALYST, AppUserRole.APPROVER])
    ),
):
    """L-04 Observability: inference/OCR/LLM latency, task duration, error rate. Read-only, in-process."""
    summary = get_runtime_metrics_summary()
    return RuntimeMetricsResponse(
        categories={
            category: RuntimeMetricCategory(**values) for category, values in summary.items()
        }
    )
