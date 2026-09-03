"""
Module J, Category B — local dispatch scaffolding.

HARD BOUNDARY: this module performs NO outbound HTTP call and contains NO
Razorpay client. The real transport boundary (Category D in
docs/ResolveAI_Module_J_Implementation_Plan.md) is not implemented,
credentialed, or invoked anywhere here. `_default_transport` — the only
transport this module ever uses in production — always raises
`ExternalBoundaryNotImplemented` without performing any I/O.

This module's job is the *local* claim / attempt-recording / bounded
retry-backoff scaffolding around that unimplemented boundary, reusing the
exact retry pattern already established by app/worker/tasks.py's
enrich_dispute_task. It never sets OutboxStatus.SENT: that would require
a real Razorpay response to interpret, which does not exist in Category B.
"""

import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, NoReturn, Optional
from uuid import UUID

from app.core.database import SessionLocal
from app.models.module_h import ExternalActionOutbox, ExternalActionAttempt, OutboxStatus
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BASE_RETRY_DELAY_SECONDS = 30


class ExternalActionBoundaryError(Exception):
    """Base class for every failure the (unimplemented) transport seam can raise."""


class ExternalBoundaryNotImplemented(ExternalActionBoundaryError):
    """
    Raised by the default transport. The real Razorpay HTTP call is
    Category D and is not authorized/implemented. Not retried — retrying
    a call that always fails the same way for the same reason wastes
    attempts and would misleadingly resemble transient-failure handling.
    """


class SimulatedTransientTransportFailure(ExternalActionBoundaryError):
    """
    Test-only. Lets tests exercise the bounded retry/backoff scaffolding
    without any real network dependency. Never raised by production code.
    """


class SimulatedUnknownTransportResult(ExternalActionBoundaryError):
    """
    Test-only. Simulates a request that was sent but whose result is
    unknown (e.g. a timeout with no response received) — see the Module J
    plan's "Unknown result" failure semantics. Handling this must not
    assume Razorpay's real endpoint is idempotent, so it is never
    auto-retried here.
    """


def _default_transport(outbox: ExternalActionOutbox) -> NoReturn:
    """
    The Category D boundary. Always raises without performing any I/O.
    Production code never overrides this — only tests inject a different
    `transport` callable to exercise the scaffolding below.
    """
    raise ExternalBoundaryNotImplemented(
        "Category D (the real Razorpay HTTP call) is not implemented or authorized. "
        "See docs/ResolveAI_Module_J_Implementation_Plan.md."
    )


@dataclasses.dataclass(frozen=True)
class DispatchAttemptResult:
    dispatched: bool
    outbox_id: Optional[UUID]
    outcome: str  # "not_claimable" | "boundary_not_implemented" | "simulated_transient_failure" | "unknown_result"
    attempt_id: Optional[UUID]
    error_code: Optional[str]
    checked_at: datetime


def dispatch_external_action_outbox(
    db,
    outbox_id: UUID,
    transport: Callable[[ExternalActionOutbox], None] = _default_transport,
    current_time: Optional[datetime] = None,
) -> DispatchAttemptResult:
    """
    Claims one PENDING (or retry-due FAILED) outbox row, marks it
    PROCESSING, records one ExternalActionAttempt, and invokes `transport`
    — which is always the unimplemented Category D boundary in production.

    Never sets OutboxStatus.SENT. A local/simulated "success" from an
    injected test transport is deliberately NOT wired to any success
    path here — there is none in Category B.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    outbox = (
        db.query(ExternalActionOutbox)
        .filter(ExternalActionOutbox.id == outbox_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if outbox is None:
        return DispatchAttemptResult(
            dispatched=False, outbox_id=None, outcome="not_claimable",
            attempt_id=None, error_code="OUTBOX_ROW_NOT_FOUND", checked_at=current_time,
        )

    claimable_now = outbox.status == OutboxStatus.PENDING or (
        outbox.status == OutboxStatus.FAILED
        and outbox.next_attempt_at is not None
        and outbox.next_attempt_at <= current_time
        and outbox.attempt_count < MAX_ATTEMPTS
    )
    if not claimable_now:
        return DispatchAttemptResult(
            dispatched=False, outbox_id=outbox.id, outcome="not_claimable",
            attempt_id=None, error_code="OUTBOX_NOT_CLAIMABLE", checked_at=current_time,
        )

    outbox.status = OutboxStatus.PROCESSING
    outbox.attempt_count += 1
    db.flush()

    attempt = ExternalActionAttempt(
        outbox_id=outbox.id,
        attempt_no=outbox.attempt_count,
        # Locally-known metadata only — no credentials, no auth headers,
        # no Razorpay request body (none exists to record in Category B).
        request_metadata={"action_type": outbox.action_type.value, "aggregate_id": str(outbox.aggregate_id)},
        started_at=current_time,
    )
    db.add(attempt)
    db.flush()

    try:
        transport(outbox)
        # Unreachable in Category B: no transport in this codebase ever
        # returns normally. Left unimplemented deliberately — see module
        # docstring. If this line is ever reached, it is a Category D
        # feature, not a Category B one, and this scaffolding does not
        # attempt to interpret a "successful" result.
        raise AssertionError("Category B dispatch scaffolding reached an unimplemented success path")
    except ExternalBoundaryNotImplemented as exc:
        attempt.completed_at = datetime.now(timezone.utc)
        attempt.error_code = "EXTERNAL_BOUNDARY_NOT_IMPLEMENTED"
        attempt.response_metadata = {"detail": str(exc)}
        outbox.status = OutboxStatus.FAILED
        outbox.next_attempt_at = None
        db.commit()
        return DispatchAttemptResult(
            dispatched=True, outbox_id=outbox.id, outcome="boundary_not_implemented",
            attempt_id=attempt.id, error_code="EXTERNAL_BOUNDARY_NOT_IMPLEMENTED", checked_at=current_time,
        )
    except SimulatedUnknownTransportResult as exc:
        # Do not invent a safe retry policy: an unknown result is never
        # auto-retried, since Razorpay's real retry-safety/idempotency
        # behavior is undocumented (Module J plan §8.6). Terminal FAILED
        # after exactly this one attempt, distinctly error-coded so it is
        # never confused with a clean local failure.
        attempt.completed_at = datetime.now(timezone.utc)
        attempt.error_code = "UNKNOWN_RESULT"
        attempt.response_metadata = {"detail": str(exc)}
        outbox.status = OutboxStatus.FAILED
        outbox.next_attempt_at = None
        db.commit()
        return DispatchAttemptResult(
            dispatched=True, outbox_id=outbox.id, outcome="unknown_result",
            attempt_id=attempt.id, error_code="UNKNOWN_RESULT", checked_at=current_time,
        )
    except SimulatedTransientTransportFailure as exc:
        attempt.completed_at = datetime.now(timezone.utc)
        attempt.error_code = "SIMULATED_TRANSIENT_TRANSPORT_FAILURE"
        attempt.response_metadata = {"detail": str(exc)}
        if outbox.attempt_count < MAX_ATTEMPTS:
            countdown = BASE_RETRY_DELAY_SECONDS * (2 ** (outbox.attempt_count - 1))
            outbox.status = OutboxStatus.FAILED
            outbox.next_attempt_at = current_time + timedelta(seconds=countdown)
        else:
            outbox.status = OutboxStatus.FAILED
            outbox.next_attempt_at = None
        db.commit()
        return DispatchAttemptResult(
            dispatched=True, outbox_id=outbox.id, outcome="simulated_transient_failure",
            attempt_id=attempt.id, error_code="SIMULATED_TRANSIENT_TRANSPORT_FAILURE", checked_at=current_time,
        )


@celery_app.task(bind=True, max_retries=MAX_ATTEMPTS, default_retry_delay=BASE_RETRY_DELAY_SECONDS)
def dispatch_external_action_task(self, outbox_id: str):
    """
    Celery entry point wrapping dispatch_external_action_outbox with this
    codebase's existing retry/backoff convention (mirrors
    app/worker/tasks.py::enrich_dispute_task exactly). Not registered as
    an automatic trigger off any review-action event — see the Module J
    completion report's "trigger boundary" section for why this remains
    callable-only in Category B.
    """
    logger.info(f"dispatch_external_action_task started for outbox_id={outbox_id} (attempt {self.request.retries + 1})")
    db = SessionLocal()
    try:
        result = dispatch_external_action_outbox(db, UUID(outbox_id))
        logger.info(f"dispatch_external_action_task outcome={result.outcome} for outbox_id={outbox_id}")
        return {"outcome": result.outcome, "outbox_id": outbox_id}
    finally:
        db.close()
