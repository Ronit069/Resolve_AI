"""
Issue #16 -- structured JSON logging with a request/task correlation ID.
"""
import json
import logging

import pytest

from app.core.logging_config import (
    CorrelationIdFilter,
    JsonFormatter,
    configure_logging,
    correlation_id_from_task_args,
    correlation_id_var,
    reset_correlation_id,
    set_correlation_id,
)


def _make_record(message="hello", exc_info=None):
    record = logging.LogRecord(
        name="app.worker.tasks", level=logging.INFO, pathname=__file__,
        lineno=42, msg=message, args=(), exc_info=exc_info,
    )
    return record


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------

def test_json_formatter_produces_valid_json_with_expected_keys():
    record = _make_record()
    CorrelationIdFilter().filter(record)
    output = json.loads(JsonFormatter().format(record))

    for key in ("timestamp", "level", "logger", "message", "correlation_id", "module", "line"):
        assert key in output
    assert output["level"] == "INFO"
    assert output["logger"] == "app.worker.tasks"
    assert output["message"] == "hello"


def test_json_formatter_includes_exception_traceback():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = _make_record(message="failed", exc_info=sys.exc_info())

    CorrelationIdFilter().filter(record)
    output = json.loads(JsonFormatter().format(record))
    assert "exception" in output
    assert "ValueError: boom" in output["exception"]


def test_correlation_id_filter_attaches_current_context_value():
    token = set_correlation_id("case-123")
    try:
        record = _make_record()
        CorrelationIdFilter().filter(record)
        assert record.correlation_id == "case-123"
    finally:
        reset_correlation_id(token)


def test_correlation_id_defaults_to_placeholder_outside_any_context():
    record = _make_record()
    CorrelationIdFilter().filter(record)
    assert record.correlation_id == "-"


def test_set_and_reset_correlation_id_round_trips_nested_contexts():
    assert correlation_id_var.get() == "-"
    outer_token = set_correlation_id("outer")
    assert correlation_id_var.get() == "outer"
    inner_token = set_correlation_id("inner")
    assert correlation_id_var.get() == "inner"
    reset_correlation_id(inner_token)
    assert correlation_id_var.get() == "outer"
    reset_correlation_id(outer_token)
    assert correlation_id_var.get() == "-"


def test_configure_logging_is_idempotent():
    configure_logging()
    configure_logging()
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)


# ---------------------------------------------------------------------------
# correlation_id_from_task_args -- the Celery task_prerun heuristic
# ---------------------------------------------------------------------------

def test_prefers_explicit_case_id_kwarg():
    result = correlation_id_from_task_args(args=("ignored",), kwargs={"case_id": "case-1"}, task_id="celery-task-id")
    assert result == "case-1"


@pytest.mark.parametrize("key", ["case_id", "document_id", "validation_run_id", "outbox_id"])
def test_recognizes_every_real_task_kwarg_name(key):
    result = correlation_id_from_task_args(args=None, kwargs={key: "value-1"}, task_id="celery-task-id")
    assert result == "value-1"


def test_falls_back_to_first_positional_arg():
    result = correlation_id_from_task_args(args=("case-42", "doc-7"), kwargs={}, task_id="celery-task-id")
    assert result == "case-42"


def test_falls_back_to_celery_task_id_when_no_identifier_available():
    result = correlation_id_from_task_args(args=(), kwargs={}, task_id="celery-task-id")
    assert result == "celery-task-id"
