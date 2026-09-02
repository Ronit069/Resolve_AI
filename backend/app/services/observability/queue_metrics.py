"""
H-22 Observability — read-only aggregate queue metrics.

Implements exactly three of H-22's six requirement metrics: queue age,
cases near deadline, and review turnaround. Submission success rate and
API errors (blocked on H-13's unpopulated external-action outbox) and
won/lost/closed outcome metrics (blocked on H-19's unpopulated
dispute_outcomes table) are deliberately not implemented here.

Every function is a pure read: no db.add/flush/commit anywhere in this
module. current_time defaults to datetime.now(timezone.utc) and exists
only so callers (tests) can pin a deterministic clock directly; the HTTP
endpoint never accepts a client-supplied time.

Review turnaround identifies the finalizing ReviewAction as the row with
MAX(created_at) per queue_item_id, restricted to queue items that have
reached QueueStatus.DONE. This is not a guess: app/api/endpoints/review.py
rejects any further review-action submission once a queue item is DONE
(see submit_review_action's QueueStatus.DONE check), and every code path
that sets queue_status = DONE does so in the same commit as creating the
new ReviewAction row. So the DONE transition always coincides with the
creation of that queue item's chronologically last ReviewAction, and no
ReviewAction can ever be created afterward — making MAX(created_at) among
a DONE item's actions provably equal to its finalizing action.
"""

import dataclasses
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.shared import Case
from app.models.module_h import ReviewQueueItem, ReviewAction, QueueStatus

NEAR_DEADLINE_THRESHOLD_HOURS = 24


def _resolve_current_time(current_time: Optional[datetime]) -> datetime:
    if current_time is None:
        return datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=timezone.utc)
    return current_time


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclasses.dataclass(frozen=True)
class QueueAgeMetrics:
    active_item_count: int
    average_age_seconds: Optional[float]
    min_age_seconds: Optional[float]
    max_age_seconds: Optional[float]


@dataclasses.dataclass(frozen=True)
class NearDeadlineMetrics:
    threshold_hours: int
    near_deadline_count: int
    expired_count: int


@dataclasses.dataclass(frozen=True)
class ReviewTurnaroundMetrics:
    completed_item_count: int
    average_turnaround_seconds: Optional[float]
    min_turnaround_seconds: Optional[float]
    max_turnaround_seconds: Optional[float]


def _active_queue_item_created_ats(db: Session, merchant_id: UUID) -> List[datetime]:
    rows = (
        db.query(ReviewQueueItem.created_at)
        .join(Case, Case.case_id == ReviewQueueItem.case_id)
        .filter(
            Case.merchant_id == merchant_id,
            ReviewQueueItem.queue_status != QueueStatus.DONE,
        )
        .all()
    )
    return [_as_aware(r[0]) for r in rows]


def compute_queue_age_metrics(
    db: Session,
    merchant_id: UUID,
    current_time: Optional[datetime] = None,
) -> QueueAgeMetrics:
    """H-22: queue age over active (non-DONE) ReviewQueueItems only."""
    now = _resolve_current_time(current_time)
    created_ats = _active_queue_item_created_ats(db, merchant_id)

    if not created_ats:
        return QueueAgeMetrics(
            active_item_count=0,
            average_age_seconds=None,
            min_age_seconds=None,
            max_age_seconds=None,
        )

    ages = [(now - created_at).total_seconds() for created_at in created_ats]
    return QueueAgeMetrics(
        active_item_count=len(ages),
        average_age_seconds=sum(ages) / len(ages),
        min_age_seconds=min(ages),
        max_age_seconds=max(ages),
    )


def compute_near_deadline_metrics(
    db: Session,
    merchant_id: UUID,
    current_time: Optional[datetime] = None,
) -> NearDeadlineMetrics:
    """
    H-22: cases near deadline among active (non-DONE) ReviewQueueItems.

    near_deadline_count: current_time < respond_by <= current_time + 24h.
    expired_count: respond_by <= current_time. Reported separately from
    near_deadline_count so an already-expired item is never silently
    counted as "near deadline".
    """
    now = _resolve_current_time(current_time)
    threshold = now.timestamp() + (NEAR_DEADLINE_THRESHOLD_HOURS * 3600)

    rows = (
        db.query(ReviewQueueItem.respond_by)
        .join(Case, Case.case_id == ReviewQueueItem.case_id)
        .filter(
            Case.merchant_id == merchant_id,
            ReviewQueueItem.queue_status != QueueStatus.DONE,
        )
        .all()
    )

    near_deadline_count = 0
    expired_count = 0
    for (respond_by,) in rows:
        respond_by = _as_aware(respond_by)
        respond_by_ts = respond_by.timestamp()
        if respond_by_ts <= now.timestamp():
            expired_count += 1
        elif respond_by_ts <= threshold:
            near_deadline_count += 1

    return NearDeadlineMetrics(
        threshold_hours=NEAR_DEADLINE_THRESHOLD_HOURS,
        near_deadline_count=near_deadline_count,
        expired_count=expired_count,
    )


def compute_review_turnaround_metrics(
    db: Session,
    merchant_id: UUID,
    current_time: Optional[datetime] = None,
) -> ReviewTurnaroundMetrics:
    """
    H-22: review turnaround = finalizing ReviewAction.created_at -
    ReviewQueueItem.created_at, for queue items that have reached DONE.

    The finalizing action is identified as the ReviewAction with
    MAX(created_at) per queue_item_id — see module docstring for why this
    is provably correct rather than an assumption.
    """
    now = _resolve_current_time(current_time)

    finalizing_action_subq = (
        db.query(
            ReviewAction.queue_item_id.label("queue_item_id"),
            func.max(ReviewAction.created_at).label("finalized_at"),
        )
        .group_by(ReviewAction.queue_item_id)
        .subquery()
    )

    rows = (
        db.query(ReviewQueueItem.created_at, finalizing_action_subq.c.finalized_at)
        .join(Case, Case.case_id == ReviewQueueItem.case_id)
        .join(
            finalizing_action_subq,
            finalizing_action_subq.c.queue_item_id == ReviewQueueItem.id,
        )
        .filter(
            Case.merchant_id == merchant_id,
            ReviewQueueItem.queue_status == QueueStatus.DONE,
        )
        .all()
    )

    if not rows:
        return ReviewTurnaroundMetrics(
            completed_item_count=0,
            average_turnaround_seconds=None,
            min_turnaround_seconds=None,
            max_turnaround_seconds=None,
        )

    durations = [
        (_as_aware(finalized_at) - _as_aware(created_at)).total_seconds()
        for created_at, finalized_at in rows
    ]
    return ReviewTurnaroundMetrics(
        completed_item_count=len(durations),
        average_turnaround_seconds=sum(durations) / len(durations),
        min_turnaround_seconds=min(durations),
        max_turnaround_seconds=max(durations),
    )
