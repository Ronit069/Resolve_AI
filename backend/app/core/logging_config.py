"""
Structured JSON logging with a request/task correlation ID (issue #16).

Before this, every module did `logger = logging.getLogger(__name__)` and
relied on whatever default handler Python/uvicorn/Celery happened to
install -- no consistent format, and no way to trace one failure across
the ingestion -> enrichment -> evidence -> validation -> scoring Celery
task chain without manually grepping timestamps.

Every one of this project's Celery tasks (app/worker/tasks.py,
risk_scoring_tasks.py, validation_tasks.py, external_action_tasks.py)
takes exactly one business identifier (case_id / document_id /
validation_run_id / outbox_id) as its first positional argument --
app/worker/celery_app.py's task_prerun/task_postrun signal handlers use
that directly as the correlation ID, so no individual task function
needed to change to get this. app/main.py's HTTP middleware does the
same for API requests (X-Request-ID in, same value echoed back out).
"""
import contextvars
import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "-"),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exception"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(payload)


def configure_logging() -> None:
    """
    Idempotent -- safe to call from app.main, app.worker.celery_app, and
    test fixtures without stacking duplicate handlers.
    """
    from app.core.config import settings

    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationIdFilter())

    root.handlers = [handler]


def set_correlation_id(value: str) -> contextvars.Token:
    return correlation_id_var.set(value)


def reset_correlation_id(token: contextvars.Token) -> None:
    correlation_id_var.reset(token)


def correlation_id_from_task_args(args: Optional[tuple], kwargs: Optional[dict], task_id: str) -> str:
    """
    Picks the business identifier a Celery task was actually called with
    (preferring an explicit kwarg name, falling back to the first
    positional argument every real task here is called with), or the
    task's own Celery-assigned ID if neither is present.
    """
    if kwargs:
        for key in ("case_id", "document_id", "validation_run_id", "outbox_id"):
            if key in kwargs and kwargs[key] is not None:
                return str(kwargs[key])
    if args:
        return str(args[0])
    return task_id
