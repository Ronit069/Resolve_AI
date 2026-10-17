from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
import uuid

from app.core.config import settings
from app.core.database import engine, Base, get_db
from app.models.shared import Merchant, AppUser
from app.api.endpoints import auth, webhooks, dev, enrichment, evidence, intelligence, validation, generation, review, observability, queue, audit, outcome_webhooks, runtime_observability, model_evaluation, drift_monitoring
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
