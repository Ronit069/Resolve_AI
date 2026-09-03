"""
Module J — outbox dispatch worker.

Category B established the local claim / attempt-recording / bounded
retry-backoff scaffolding around an always-raising stub transport
(`_default_transport`, which performs no I/O and is untouched here — see
below). Category D adds the real Razorpay HTTP boundary
(`app.services.external_action.razorpay_client`) and wires it in as a
*separate* production transport (`make_razorpay_production_transport`),
used only by the Celery entry point `dispatch_external_action_task`.

`dispatch_external_action_outbox`'s own default parameter is still
`_default_transport` — every caller (test or otherwise) that does not
pass an explicit `transport=` gets the exact same no-I/O stub Category B
always used. This keeps every Category B scaffolding test valid
unchanged; only the real Celery task path picks up the Category D
transport.

Transaction boundary: dispatch happens across two commits so the
outbound HTTP call is never made while holding the initial
`with_for_update` row lock. (1) claim the row, flip it to PROCESSING,
record the attempt start, commit — releasing the lock. (2) invoke
`transport` (for the real transport, this performs the actual Razorpay
call), then record the attempt outcome and final domain state in a
second commit.

Retry classification (frozen; see docs/ResolveAI_Module_J_Implementation_Plan.md
Category D addendum):
  - RazorpayRateLimited (429): the only retryable condition — bounded,
    exponential backoff, same MAX_ATTEMPTS/formula as Category B's
    simulated transient-failure path.
  - RazorpayValidationError, RazorpayAuthError, RazorpayLiveModeNotAllowed,
    RazorpayDocumentLinkMissing: terminal, never retried — retrying an
    invalid or misconfigured request fails identically every time.
  - RazorpayUnknownResult (5xx, a timeout/connection error, or an
    unparseable/malformed 2xx body): terminal and never auto-retried.
    Razorpay documents no idempotency mechanism for the document-upload
    or dispute-contest endpoints, so whether the request was actually
    applied server-side is unknown; retrying could double-submit.
"""

import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, NoReturn, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.storage import storage_client
from app.models.module_c import EvidenceDocument
from app.models.module_h import (
    ContestPackage,
    ContestPackageStatus,
    ContestSubmission,
    ExternalActionAttempt,
    ExternalActionOutbox,
    ExternalActionType,
    OutboxStatus,
    RazorpayDocumentLink,
    SubmissionStatus,
)
from app.services.external_action.razorpay_client import (
    RazorpayAuthError,
    RazorpayClient,
    RazorpayLiveModeNotAllowed,
    RazorpayRateLimited,
    RazorpayUnknownResult,
    RazorpayValidationError,
)
from app.services.external_action.razorpay_request_builder import (
    RazorpayDocumentLinkMissing,
    build_contest_request,
    build_upload_document_request,
)
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BASE_RETRY_DELAY_SECONDS = 30


class ExternalActionBoundaryError(Exception):
    """Base class for every failure the (test-only) simulated transport seam can raise."""


class ExternalBoundaryNotImplemented(ExternalActionBoundaryError):
    """
    Raised by the default transport. Not retried — retrying a call that
    always fails the same way for the same reason wastes attempts and
    would misleadingly resemble transient-failure handling.
    """


class SimulatedTransientTransportFailure(ExternalActionBoundaryError):
    """
    Test-only. Lets tests exercise the bounded retry/backoff scaffolding
    without any real network dependency. Never raised by production code.
    """


class SimulatedUnknownTransportResult(ExternalActionBoundaryError):
    """
    Test-only. Simulates a request that was sent but whose result is
    unknown (e.g. a timeout with no response received). Never
    auto-retried here — mirrors RazorpayUnknownResult's real-transport
    treatment below.
    """


def _default_transport(outbox: ExternalActionOutbox) -> NoReturn:
    """
    The function-level default for dispatch_external_action_outbox.
    Always raises without performing any I/O. Only the Celery entry point
    (dispatch_external_action_task) overrides this with the real
    Category D production transport — every other/test caller gets this
    exact no-op stub.
    """
    raise ExternalBoundaryNotImplemented(
        "No transport was supplied to dispatch_external_action_outbox; the default "
        "transport performs no I/O by design."
    )


def _jsonable(value):
    if value is None or isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return str(value)


def _terminal_error_code(exc: Exception) -> str:
    if isinstance(exc, RazorpayLiveModeNotAllowed):
        return "RAZORPAY_LIVE_MODE_NOT_ALLOWED"
    if isinstance(exc, RazorpayDocumentLinkMissing):
        return "RAZORPAY_DOCUMENT_LINK_MISSING"
    if isinstance(exc, RazorpayAuthError):
        return "RAZORPAY_AUTH_ERROR"
    if isinstance(exc, RazorpayValidationError):
        return "RAZORPAY_VALIDATION_ERROR"
    return "RAZORPAY_TERMINAL_ERROR"


def _terminal_response_metadata(exc: Exception) -> dict:
    if isinstance(exc, RazorpayValidationError):
        return {
            "detail": str(exc),
            "razorpay_error_code": exc.error_code,
            "razorpay_description": exc.description,
            "raw_body": _jsonable(exc.raw_body),
        }
    if isinstance(exc, RazorpayAuthError):
        return {"detail": str(exc), "raw_body": _jsonable(exc.raw_body)}
    return {"detail": str(exc)}


def _persist_success_side_effects(db: Session, outbox: ExternalActionOutbox, response: dict, current_time: datetime) -> None:
    """
    Writes the domain-specific rows a successful Razorpay response
    authorizes: a RazorpayDocumentLink for UPLOAD_DOCUMENT, or a
    ContestSubmission (and, for a submit action, the terminal
    ContestPackage.status=SUBMITTED transition) for CONTEST_DRAFT/
    CONTEST_SUBMIT. Uses only response fields Razorpay actually
    returned — never fabricates a document/dispute ID.
    """
    if outbox.action_type == ExternalActionType.UPLOAD_DOCUMENT:
        document = (
            db.query(EvidenceDocument)
            .filter(EvidenceDocument.document_id == outbox.aggregate_id)
            .populate_existing()
            .first()
        )
        db.add(RazorpayDocumentLink(
            document_id=outbox.aggregate_id,
            razorpay_document_id=response.get("id"),
            purpose="dispute_evidence",
            mime_type=document.mime_type if document is not None else "application/octet-stream",
            size_bytes=document.file_size_bytes if document is not None else 0,
            uploaded_at=current_time,
            external_response_json=response,
        ))
        return

    package = (
        db.query(ContestPackage)
        .filter(ContestPackage.id == outbox.aggregate_id)
        .populate_existing()
        .first()
    )
    action = (outbox.payload_json or {}).get("action")
    db.add(ContestSubmission(
        contest_package_id=outbox.aggregate_id,
        external_dispute_id=response.get("id", ""),
        action=action or "",
        external_status=response.get("status", ""),
        submitted_at=current_time if action == "submit" else None,
        razorpay_evidence_json=response.get("evidence", {}),
        response_snapshot=response,
        status=SubmissionStatus.SUCCESS,
    ))
    if package is not None and action == "submit":
        package.status = ContestPackageStatus.SUBMITTED


def make_razorpay_production_transport(
    db: Session,
    client: Optional[RazorpayClient] = None,
) -> Callable[[ExternalActionOutbox], dict]:
    """
    Builds the real Category D transport for one dispatch call, bound to
    `db` so it can resolve the document/package/link rows a Razorpay
    request needs. `client` may be injected by tests; production code
    always omits it, so a fresh RazorpayClient (and its live-mode guard)
    is constructed per call.

    Never called by dispatch_external_action_outbox's own default — only
    dispatch_external_action_task wires this in.
    """
    def _transport(outbox: ExternalActionOutbox) -> dict:
        owns_client = client is None
        rp_client = client
        try:
            if rp_client is None:
                rp_client = RazorpayClient()

            if outbox.action_type == ExternalActionType.UPLOAD_DOCUMENT:
                document = (
                    db.query(EvidenceDocument)
                    .filter(EvidenceDocument.document_id == outbox.aggregate_id)
                    .populate_existing()
                    .first()
                )
                if document is None:
                    raise RazorpayValidationError(
                        status_code=0, error_code="LOCAL_DOCUMENT_NOT_FOUND",
                        description=f"EvidenceDocument {outbox.aggregate_id} not found", raw_body=None,
                    )
                file_bytes = storage_client.download_file(document.object_key)
                request_fields = build_upload_document_request()
                response = rp_client.upload_document(
                    file_bytes=file_bytes,
                    filename=document.original_filename or str(document.document_id),
                    content_type=document.mime_type,
                    purpose=request_fields["purpose"],
                )
            else:
                package = (
                    db.query(ContestPackage)
                    .filter(ContestPackage.id == outbox.aggregate_id)
                    .populate_existing()
                    .first()
                )
                if package is None:
                    raise RazorpayValidationError(
                        status_code=0, error_code="LOCAL_CONTEST_PACKAGE_NOT_FOUND",
                        description=f"ContestPackage {outbox.aggregate_id} not found", raw_body=None,
                    )
                action = (outbox.payload_json or {}).get("action")
                built = build_contest_request(db, outbox.case_id, package, action)
                response = rp_client.contest_dispute(built.razorpay_dispute_id, built.body)

            if not isinstance(response, dict) or not response.get("id"):
                raise RazorpayUnknownResult(
                    "Razorpay returned a 2xx response missing the expected 'id' field",
                    raw_body=response,
                )
            return response
        finally:
            if owns_client and rp_client is not None:
                rp_client.close()

    return _transport


@dataclasses.dataclass(frozen=True)
class DispatchAttemptResult:
    dispatched: bool
    outbox_id: Optional[UUID]
    # "not_claimable" | "boundary_not_implemented" | "simulated_transient_failure" |
    # "unknown_result" | "razorpay_rate_limited" | "razorpay_validation_error" |
    # "razorpay_auth_error" | "razorpay_live_mode_not_allowed" |
    # "razorpay_document_link_missing" | "sent"
    outcome: str
    attempt_id: Optional[UUID]
    error_code: Optional[str]
    checked_at: datetime


def dispatch_external_action_outbox(
    db,
    outbox_id: UUID,
    transport: Callable[[ExternalActionOutbox], Optional[dict]] = _default_transport,
    current_time: Optional[datetime] = None,
) -> DispatchAttemptResult:
    """
    Claims one PENDING (or retry-due FAILED) outbox row, marks it
    PROCESSING, records one ExternalActionAttempt, and invokes
    `transport`. On a real (Category D) success — transport returns a
    response dict instead of raising — writes the domain rows that
    response authorizes and sets OutboxStatus.SENT.
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
        # no raw Razorpay request body.
        request_metadata={"action_type": outbox.action_type.value, "aggregate_id": str(outbox.aggregate_id)},
        started_at=current_time,
    )
    db.add(attempt)
    db.flush()

    # First commit: claims the row and records the attempt start, then
    # releases the `with_for_update` row lock before the transport call
    # below (which, for the real transport, is a genuine network call).
    db.commit()

    try:
        response = transport(outbox)
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
        # behavior is undocumented. Terminal FAILED after exactly this
        # one attempt, distinctly error-coded so it is never confused
        # with a clean local failure.
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
    except RazorpayRateLimited as exc:
        # 429: the only real-transport condition treated as retryable —
        # same bounded exponential backoff as the simulated transient
        # failure above.
        attempt.completed_at = datetime.now(timezone.utc)
        attempt.error_code = "RAZORPAY_RATE_LIMITED"
        attempt.response_metadata = {"detail": str(exc), "raw_body": _jsonable(exc.raw_body)}
        if outbox.attempt_count < MAX_ATTEMPTS:
            countdown = BASE_RETRY_DELAY_SECONDS * (2 ** (outbox.attempt_count - 1))
            outbox.status = OutboxStatus.FAILED
            outbox.next_attempt_at = current_time + timedelta(seconds=countdown)
        else:
            outbox.status = OutboxStatus.FAILED
            outbox.next_attempt_at = None
        db.commit()
        return DispatchAttemptResult(
            dispatched=True, outbox_id=outbox.id, outcome="razorpay_rate_limited",
            attempt_id=attempt.id, error_code="RAZORPAY_RATE_LIMITED", checked_at=current_time,
        )
    except RazorpayUnknownResult as exc:
        # Frozen Product Owner decision: 5xx, no response (timeout/
        # connection error), and a malformed/unparseable 2xx body all
        # collapse into the same terminal, never-auto-retried UNKNOWN_RESULT
        # treatment as SimulatedUnknownTransportResult above — Razorpay
        # documents no idempotency mechanism for these endpoints, so
        # auto-retrying could double-submit.
        attempt.completed_at = datetime.now(timezone.utc)
        attempt.error_code = "UNKNOWN_RESULT"
        attempt.http_status = exc.status_code
        attempt.response_metadata = {"detail": str(exc), "http_status": exc.status_code, "raw_body": _jsonable(exc.raw_body)}
        outbox.status = OutboxStatus.FAILED
        outbox.next_attempt_at = None
        db.commit()
        return DispatchAttemptResult(
            dispatched=True, outbox_id=outbox.id, outcome="unknown_result",
            attempt_id=attempt.id, error_code="UNKNOWN_RESULT", checked_at=current_time,
        )
    except (RazorpayValidationError, RazorpayAuthError, RazorpayLiveModeNotAllowed, RazorpayDocumentLinkMissing) as exc:
        # Terminal, never retried: an invalid request, a credential/
        # config problem, or a missing document link fails identically on
        # every retry attempt.
        error_code = _terminal_error_code(exc)
        attempt.completed_at = datetime.now(timezone.utc)
        attempt.error_code = error_code
        attempt.response_metadata = _terminal_response_metadata(exc)
        if isinstance(exc, (RazorpayValidationError, RazorpayAuthError)):
            attempt.http_status = exc.status_code
        outbox.status = OutboxStatus.FAILED
        outbox.next_attempt_at = None
        db.commit()
        return DispatchAttemptResult(
            dispatched=True, outbox_id=outbox.id, outcome=error_code.lower(),
            attempt_id=attempt.id, error_code=error_code, checked_at=current_time,
        )
    else:
        attempt.completed_at = datetime.now(timezone.utc)
        attempt.error_code = None
        attempt.response_metadata = _jsonable(response)
        outbox.status = OutboxStatus.SENT
        outbox.next_attempt_at = None
        _persist_success_side_effects(db, outbox, response or {}, current_time)
        db.commit()
        return DispatchAttemptResult(
            dispatched=True, outbox_id=outbox.id, outcome="sent",
            attempt_id=attempt.id, error_code=None, checked_at=current_time,
        )


@celery_app.task(bind=True, max_retries=MAX_ATTEMPTS, default_retry_delay=BASE_RETRY_DELAY_SECONDS)
def dispatch_external_action_task(self, outbox_id: str):
    """
    Celery entry point. This is the only place in the codebase that
    invokes dispatch_external_action_outbox with the real Category D
    production transport (make_razorpay_production_transport) — not
    registered as an automatic trigger off any review-action event, see
    the Module J plan's "trigger boundary" notes.
    """
    logger.info(f"dispatch_external_action_task started for outbox_id={outbox_id} (attempt {self.request.retries + 1})")
    db = SessionLocal()
    try:
        transport = make_razorpay_production_transport(db)
        result = dispatch_external_action_outbox(db, UUID(outbox_id), transport=transport)
        logger.info(f"dispatch_external_action_task outcome={result.outcome} for outbox_id={outbox_id}")
        return {"outcome": result.outcome, "outbox_id": outbox_id}
    finally:
        db.close()
