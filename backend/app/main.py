from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
import uuid

from app.core.config import settings
from app.core.database import engine, Base, get_db
from app.models.shared import Merchant, AppUser
from app.api.endpoints import webhooks, dev, enrichment, evidence, intelligence, validation, generation, review, observability
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend Foundation for Modules A, B, and C",
    version="0.2.0"
)



@app.get("/health")
def health_check():
    return {"status": "ok", "project": settings.PROJECT_NAME}

app.include_router(webhooks.router, prefix="/api/v1/webhooks", tags=["webhooks"])
app.include_router(dev.router, prefix="/api/v1/dev", tags=["dev"])
app.include_router(enrichment.router, prefix="/api/v1/cases", tags=["enrichment"])
app.include_router(evidence.router, prefix="/api/v1/cases", tags=["evidence"])
app.include_router(intelligence.router, prefix="/api/v1/documents", tags=["intelligence"])
app.include_router(validation.router, prefix="/api/v1/cases", tags=["validation"])
app.include_router(generation.router, prefix="/api/v1/cases", tags=["generation"])
app.include_router(review.router, prefix="/api/v1/cases", tags=["review"])
app.include_router(observability.router, prefix="/api/v1/observability", tags=["observability"])
