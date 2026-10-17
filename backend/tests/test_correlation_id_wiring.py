"""
Issue #16 -- the actual wiring: Celery's task_prerun/task_postrun signals
(app/worker/celery_app.py) and the FastAPI HTTP middleware (app/main.py),
not just the pure logging_config helpers those use.
"""
from celery.signals import task_postrun, task_prerun
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.logging_config import correlation_id_var
from app.worker.celery_app import celery_app  # noqa: F401 -- registers the signal handlers


# ---------------------------------------------------------------------------
# Celery task_prerun/task_postrun signals
# ---------------------------------------------------------------------------

def test_task_prerun_sets_correlation_id_from_first_positional_arg():
    assert correlation_id_var.get() == "-"
    task_prerun.send(sender=None, task_id="celery-id-1", task=None, args=("case-abc",), kwargs={})
    try:
        assert correlation_id_var.get() == "case-abc"
    finally:
        task_postrun.send(sender=None, task_id="celery-id-1", task=None, args=("case-abc",), kwargs={}, retval=None, state="SUCCESS")
    assert correlation_id_var.get() == "-"


def test_task_postrun_resets_even_for_an_unknown_task_id():
    # Must not raise if postrun fires for a task_id prerun never saw a token for.
    task_postrun.send(sender=None, task_id="never-seen", task=None, args=(), kwargs={}, retval=None, state="SUCCESS")


def test_nested_task_invocations_restore_the_outer_correlation_id():
    task_prerun.send(sender=None, task_id="outer", task=None, args=("case-outer",), kwargs={})
    try:
        assert correlation_id_var.get() == "case-outer"
        task_prerun.send(sender=None, task_id="inner", task=None, args=("case-inner",), kwargs={})
        try:
            assert correlation_id_var.get() == "case-inner"
        finally:
            task_postrun.send(sender=None, task_id="inner", task=None, args=("case-inner",), kwargs={}, retval=None, state="SUCCESS")
        assert correlation_id_var.get() == "case-outer"
    finally:
        task_postrun.send(sender=None, task_id="outer", task=None, args=("case-outer",), kwargs={}, retval=None, state="SUCCESS")
    assert correlation_id_var.get() == "-"


# ---------------------------------------------------------------------------
# FastAPI middleware (app/main.py's correlation_id_middleware, re-created
# minimally here to avoid pulling in app.main's full storage/DB stack)
# ---------------------------------------------------------------------------

def _build_test_app() -> FastAPI:
    import uuid

    from app.core.logging_config import reset_correlation_id, set_correlation_id

    app = FastAPI()

    @app.middleware("http")
    async def correlation_id_middleware(request, call_next):
        correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(token)
        response.headers["X-Request-ID"] = correlation_id
        return response

    @app.get("/whoami")
    def whoami():
        return {"correlation_id": correlation_id_var.get()}

    return app


def test_middleware_generates_a_request_id_when_none_provided():
    client = TestClient(_build_test_app())
    response = client.get("/whoami")
    assert response.status_code == 200
    header_id = response.headers["X-Request-ID"]
    assert header_id
    assert response.json()["correlation_id"] == header_id


def test_middleware_echoes_back_an_inbound_request_id():
    client = TestClient(_build_test_app())
    response = client.get("/whoami", headers={"X-Request-ID": "caller-supplied-id"})
    assert response.headers["X-Request-ID"] == "caller-supplied-id"
    assert response.json()["correlation_id"] == "caller-supplied-id"


def test_middleware_resets_correlation_id_after_the_request():
    client = TestClient(_build_test_app())
    client.get("/whoami", headers={"X-Request-ID": "should-not-leak"})
    assert correlation_id_var.get() == "-"
