"""
Module I — I-07 Audit/Activity feed.

Read-only, merchant-and-case-scoped merge of two existing, frozen, already-
populated tables (AuditLog from Module A/shared, ReviewAction from Module H)
into one normalized, chronologically-ordered feed. No writes, no new
persistence, no change to either source table.
"""

import dataclasses
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.shared import AuditLog
from app.models.module_h import ReviewAction

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


@dataclasses.dataclass(frozen=True)
class ActivityEvent:
    event_type: str  # "AUDIT_LOG" | "REVIEW_ACTION"
    event_id: str
    case_id: UUID
    actor_user_id: Optional[UUID]
    action: str
    details: Optional[str]
    created_at: object


@dataclasses.dataclass(frozen=True)
class AuditFeedResult:
    items: List[ActivityEvent]
    total_count: int
    limit: int
    offset: int


def _normalize_audit_log(row: AuditLog) -> ActivityEvent:
    return ActivityEvent(
        event_type="AUDIT_LOG",
        event_id=str(row.audit_id),
        case_id=row.case_id,
        actor_user_id=row.user_id,
        action=row.action,
        details=row.details,
        created_at=row.created_at,
    )


def _normalize_review_action(row: ReviewAction) -> ActivityEvent:
    detail_parts = []
    if row.override_reason_code:
        detail_parts.append(f"override_reason_code={row.override_reason_code}")
    if row.notes:
        detail_parts.append(f"notes={row.notes}")
    details = "; ".join(detail_parts) if detail_parts else None
    return ActivityEvent(
        event_type="REVIEW_ACTION",
        event_id=str(row.id),
        case_id=row.case_id,
        actor_user_id=row.reviewer_id,
        action=row.action.value,
        details=details,
        created_at=row.created_at,
    )


def get_case_activity_feed(
    db: Session,
    case_id: UUID,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> AuditFeedResult:
    """
    Merge AuditLog and ReviewAction rows for one case into one deterministic,
    chronologically-ordered feed. Caller is responsible for verifying the
    case belongs to the current merchant before calling this (tenant check
    happens at the endpoint layer against Case, same as every other Module I
    endpoint, so a case that doesn't belong to the merchant never reaches
    this function).
    """
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)

    audit_rows = (
        db.query(AuditLog)
        .filter(AuditLog.case_id == case_id)
        .order_by(AuditLog.created_at.desc())
        .all()
    )
    review_action_rows = (
        db.query(ReviewAction)
        .filter(ReviewAction.case_id == case_id)
        .order_by(ReviewAction.created_at.desc())
        .all()
    )

    events = [_normalize_audit_log(r) for r in audit_rows] + [
        _normalize_review_action(r) for r in review_action_rows
    ]

    # Deterministic merge: created_at DESC, then event_type, then event_id.
    # The (event_type, event_id) tie-break carries no business meaning; it
    # exists only so two events sharing a timestamp always sort identically
    # across repeated calls.
    events.sort(key=lambda e: (e.created_at, e.event_type, e.event_id), reverse=True)

    total_count = len(events)
    page = events[offset : offset + limit]

    return AuditFeedResult(items=page, total_count=total_count, limit=limit, offset=offset)
