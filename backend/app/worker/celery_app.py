from celery import Celery
from celery.signals import task_postrun, task_prerun

from app.core.config import settings
from app.core.error_tracking import init_sentry
from app.core.logging_config import configure_logging, correlation_id_from_task_args, reset_correlation_id, set_correlation_id

configure_logging()
init_sentry(celery=True)

celery_app = Celery(
    "resolveai_worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=['app.worker.tasks', 'app.worker.external_action_tasks', 'app.worker.validation_tasks', 'app.worker.risk_scoring_tasks']
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
)

# Issue #16: every log line a task emits carries the same correlation_id
# as every other task in the same chain (ingestion -> enrichment ->
# evidence -> validation -> scoring), without any individual task
# function needing to change -- see correlation_id_from_task_args's
# docstring for why args[0] is always the right identifier here.
_correlation_id_tokens = {}


@task_prerun.connect
def _set_task_correlation_id(task_id, task, args=None, kwargs=None, **_extra):
    correlation_id = correlation_id_from_task_args(args, kwargs, task_id)
    _correlation_id_tokens[task_id] = set_correlation_id(correlation_id)


@task_postrun.connect
def _reset_task_correlation_id(task_id, **_extra):
    token = _correlation_id_tokens.pop(task_id, None)
    if token is not None:
        reset_correlation_id(token)

# Example task to verify worker is running
@celery_app.task(name="foundation.ping")
def ping_task():
    return {"status": "ok", "message": "Celery worker is running"}
