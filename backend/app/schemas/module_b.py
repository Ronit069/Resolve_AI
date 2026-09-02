"""
Module B — Pydantic schemas for provider data structures, enrichment models,
and API request/response contracts.

All money values are integer minor units (paise/cents).
All timestamps are timezone-aware UTC.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ---------------------------------------------------------------------------
# Enrichment entity status — explicit representation of lookup outcomes
# ---------------------------------------------------------------------------

class LookupStatus(str, Enum):
    """Outcome of a single provider lookup."""
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    LOOKUP_FAILED = "LOOKUP_FAILED"


# ---------------------------------------------------------------------------
# Canonical provider data structures (returned by provider adapters)
# ---------------------------------------------------------------------------

class PaymentData(BaseModel):
    """Canonical payment data returned by PaymentProvider."""
    external_payment_id: str
    external_order_id: Optional[str] = None
    amount_minor: int
    currency: str = Field(max_length=3)
    status: str
    method: Optional[str] = None
    network: Optional[str] = None
    captured: Optional[bool] = None
    created_at_source: Optional[datetime] = None


class OrderData(BaseModel):
    """Canonical order data returned by OrderProvider."""
    external_order_id: str
    merchant_order_ref: Optional[str] = None
    order_amount_minor: Optional[int] = None
    currency: Optional[str] = Field(default=None, max_length=3)
    order_status: Optional[str] = None
    customer_ref_hash: Optional[str] = None
    product_description: Optional[str] = None
    quantity: Optional[int] = None
    created_at_source: Optional[datetime] = None


class ShipmentData(BaseModel):
    """Canonical shipment data returned by ShipmentProvider."""
    shipment_id: str
    external_order_id: str
    courier: Optional[str] = None
    tracking_id: Optional[str] = None
    dispatch_at: Optional[datetime] = None
    delivery_at: Optional[datetime] = None
    delivery_status: Optional[str] = None
    delivery_address_hash: Optional[str] = None
    recipient_confirmation: Optional[bool] = None


class RefundData(BaseModel):
    """Canonical refund data returned by RefundProvider."""
    external_refund_id: str
    external_payment_id: str
    refund_amount_minor: int = Field(gt=0)
    status: str
    refund_reason: Optional[str] = None
    refund_at: Optional[datetime] = None


class CustomerHistoryData(BaseModel):
    """Privacy-minimized customer history aggregates."""
    customer_ref_hash: str
    account_age_days: Optional[int] = None
    previous_order_count: int = 0
    previous_dispute_count: int = 0
    previous_refund_count: int = 0
    refund_rate: Optional[float] = None
    dispute_rate: Optional[float] = None


# ---------------------------------------------------------------------------
# Consistency flags
# ---------------------------------------------------------------------------

class ConsistencyFlag(str, Enum):
    PAYMENT_MISMATCH = "PAYMENT_MISMATCH"
    ORDER_MISMATCH = "ORDER_MISMATCH"
    SHIPMENT_MISMATCH = "SHIPMENT_MISMATCH"
    REFUND_MISMATCH = "REFUND_MISMATCH"
    AMOUNT_INCONSISTENT = "AMOUNT_INCONSISTENT"
    TIMELINE_INCONSISTENT = "TIMELINE_INCONSISTENT"


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

class TimelineData(BaseModel):
    """Derived canonical timeline intervals. None = data not available."""
    order_at: Optional[datetime] = None
    payment_at: Optional[datetime] = None
    dispatch_at: Optional[datetime] = None
    delivery_at: Optional[datetime] = None
    dispute_at: Optional[datetime] = None
    order_to_payment_minutes: Optional[float] = None
    payment_to_dispatch_hours: Optional[float] = None
    dispatch_to_delivery_hours: Optional[float] = None
    delivery_to_dispute_days: Optional[float] = None


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------

class CompletenessData(BaseModel):
    """Per-entity and overall enrichment completeness (0.0 to 1.0)."""
    payment: float = 0.0
    order: float = 0.0
    shipment: float = 0.0
    refund: float = 0.0
    customer_history: float = 0.0
    overall: float = 0.0


# ---------------------------------------------------------------------------
# Enrichment result (internal service return)
# ---------------------------------------------------------------------------

class EnrichmentResult(BaseModel):
    """Returned by enrich_case() service function."""
    case_id: str
    status: str  # "ENRICHED", "PARTIAL", "FAILED"
    payment_status: LookupStatus = LookupStatus.NOT_AVAILABLE
    order_status: LookupStatus = LookupStatus.NOT_AVAILABLE
    shipment_status: LookupStatus = LookupStatus.NOT_AVAILABLE
    refund_status: LookupStatus = LookupStatus.NOT_AVAILABLE
    customer_history_status: LookupStatus = LookupStatus.NOT_AVAILABLE
    consistency_flags: List[str] = []
    timeline: Optional[TimelineData] = None
    completeness: Optional[CompletenessData] = None
    version: int = 1


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------

class EnrichResponse(BaseModel):
    """Response for POST /api/v1/cases/{case_id}/enrich"""
    status: str
    message: str
    case_id: str
    current_state: Optional[str] = None
