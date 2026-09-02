from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "resolveai_worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=['app.worker.tasks']
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

# Example task to verify worker is running
@celery_app.task(name="foundation.ping")
def ping_task():
    return {"status": "ok", "message": "Celery worker is running"}
