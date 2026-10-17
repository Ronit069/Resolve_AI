"""
Optional Sentry error tracking (issue #16).

Unset SENTRY_DSN (the default everywhere -- local dev, CI, and any
deployment that hasn't configured one) means init_sentry() is a
complete no-op: no Sentry SDK traffic, no import-time cost beyond the
cheap settings check. Setting SENTRY_DSN is the only thing that turns
this on -- never required for the app to start or any test to pass.
"""
import logging

logger = logging.getLogger(__name__)


def init_sentry(celery: bool = False) -> None:
    """
    Call once at process startup (app.main for the API, app.worker.celery_app
    for the worker). Never raises -- a misconfigured DSN or an import
    failure must not stop the app itself from starting, matching this
    project's existing graceful-degradation convention (StorageClient,
    champion_loader, log_training_run).
    """
    from app.core.config import settings

    if not settings.SENTRY_DSN:
        return

    try:
        import sentry_sdk

        integrations = []
        if celery:
            from sentry_sdk.integrations.celery import CeleryIntegration
            integrations.append(CeleryIntegration())
        else:
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            integrations.append(FastApiIntegration())

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            integrations=integrations,
        )
    except Exception:
        logger.warning("Sentry initialization failed; continuing without error tracking.", exc_info=True)
