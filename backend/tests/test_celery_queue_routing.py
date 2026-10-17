"""
Issue #17 -- process_evidence_document_task (the heavy PDF/image
preprocessing + real Tesseract OCR task, issue #8) is routed to its own
"documents" queue so k8s/worker-documents-deployment.yaml can scale it
independently of every other, lightweight task, which stays on the
default "celery" queue.
"""
from app.worker.celery_app import celery_app


def _queue_name_for(task_name: str) -> str:
    route = celery_app.amqp.router.route({}, task_name)
    return route["queue"].name


def test_document_processing_task_routed_to_its_own_queue():
    assert _queue_name_for("app.worker.tasks.process_evidence_document_task") == "documents"


def test_every_other_real_task_stays_on_the_default_queue():
    for task_name in (
        "app.worker.tasks.enrich_dispute_task",
        "app.worker.tasks.scan_evidence_task",
        "app.worker.risk_scoring_tasks.score_risk_task",
        "app.worker.validation_tasks.execute_evidence_validation",
        "app.worker.external_action_tasks.dispatch_external_action_task",
        "foundation.ping",
    ):
        assert _queue_name_for(task_name) == "celery"
