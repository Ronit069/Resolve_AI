from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
import uuid

from app.core.config import settings
from app.core.database import engine, Base, get_db
from app.core.logging_config import configure_logging, set_correlation_id, reset_correlation_id
from app.core.error_tracking import init_sentry
from app.models.shared import Merchant, AppUser
from app.api.endpoints import auth, webhooks, dev, enrichment, evidence, intelligence, validation, generation, review, observability, queue, audit, outcome_webhooks, runtime_observability, model_evaluation, drift_monitoring

configure_logging()
init_sentry()

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend Foundation for Modules A, B, and C",
    version="0.2.0"
)

# Module I: minimal, origin-restricted CORS for the browser frontend.
# No wildcard. Credentials travel as a bearer token in the Authorization
# header (or, dev-only, X-User-Id), never a cookie, so allow_credentials
# stays False.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.CORS_ALLOWED_ORIGIN],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "X-User-Id", "Content-Type"],
    allow_credentials=False,
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """
    Issue #16: every log line emitted while handling this request carries
    the same correlation_id (app/core/logging_config.py's JSON formatter
    attaches it automatically) -- reuses an inbound X-Request-ID so a
    caller's own trace ID survives, generates one otherwise, and always
    echoes it back so the caller can correlate on their side too.
    """
    correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    token = set_correlation_id(correlation_id)
    try:
        response = await call_next(request)
    finally:
        reset_correlation_id(token)
    response.headers["X-Request-ID"] = correlation_id
    return response


@app.get("/health")
def health_check():
    return {"status": "ok", "project": settings.PROJECT_NAME}

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(webhooks.router, prefix="/api/v1/webhooks", tags=["webhooks"])
app.include_router(dev.router, prefix="/api/v1/dev", tags=["dev"])
app.include_router(enrichment.router, prefix="/api/v1/cases", tags=["enrichment"])
app.include_router(evidence.router, prefix="/api/v1/cases", tags=["evidence"])
app.include_router(intelligence.router, prefix="/api/v1/documents", tags=["intelligence"])
app.include_router(validation.router, prefix="/api/v1/cases", tags=["validation"])
app.include_router(generation.router, prefix="/api/v1/cases", tags=["generation"])
app.include_router(review.router, prefix="/api/v1/cases", tags=["review"])
app.include_router(observability.router, prefix="/api/v1/observability", tags=["observability"])
app.include_router(queue.router, prefix="/api/v1/cases", tags=["queue"])
app.include_router(audit.router, prefix="/api/v1/cases", tags=["audit"])
app.include_router(outcome_webhooks.router, prefix="/api/v1", tags=["outcome_feedback"])
app.include_router(runtime_observability.router, prefix="/api/v1/observability", tags=["observability"])
app.include_router(model_evaluation.router, prefix="/api/v1/observability", tags=["observability"])
app.include_router(drift_monitoring.router, prefix="/api/v1/observability", tags=["observability"])
