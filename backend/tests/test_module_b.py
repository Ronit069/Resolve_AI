"""
Module B — Comprehensive Test Suite.

23 required test scenarios covering:
  - Complete / partial enrichment
  - Entity not-found for each provider
  - All 6 consistency flags
  - Timeline construction and intervals
  - Completeness calculations
  - State machine (INGESTED → ENRICHING → ENRICHED)
  - Terminal failure → revert to INGESTED
  - Transient failure → Celery retry
  - ProcessingError persistence
  - Manual enrichment endpoint
  - Duplicate/concurrent enrichment prevention
  - Module A regression
"""

import pytest
import json
import time
import hmac
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.core.database import Base, get_db
from app.core.config import settings
from app.models.module_a import WebhookEvent, Dispute, DisputeEvent
from app.models.module_b import (
    Payment, Order, Shipment, Refund, CustomerHistory, CaseEnrichment,
)
from app.models.shared import (
    Case, ProcessingState, AuditLog, ProcessingError, Merchant,
)
from app.services.enrichment import enrich_case
from app.providers.base import (
    ProviderBundle, ProviderNotFoundError, ProviderUnavailableError,
)
from app.providers.synthetic import (
    SyntheticPaymentProvider,
    SyntheticOrderProvider,
    SyntheticShipmentProvider,
    SyntheticRefundProvider,
    SyntheticCustomerHistoryProvider,
    get_synthetic_providers,
)
from app.schemas.module_b import LookupStatus


# ---------------------------------------------------------------------------
# Test database setup (SQLite)
# ---------------------------------------------------------------------------

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_b_new.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(autouse=True)
def setup_overrides():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_case_with_dispute(
    db,
    payment_id="pay_valid1",
    dispute_amount=100000,
    state=ProcessingState.INGESTED,
    dispute_created_at=None,
):
    """Create a Merchant + Case + Dispute ready for enrichment."""
    merchant = Merchant(external_merchant_id="default", name="Default Merchant")
    db.add(merchant)
    db.flush()

    case = Case(
        merchant_id=merchant.merchant_id,
        external_dispute_id="disp_test_123",
        source="synthetic",
        processing_state=state,
    )
    db.add(case)
    db.flush()

    dispute = Dispute(
        case_id=case.case_id,
        external_dispute_id="disp_test_123",
        payment_id=payment_id,
        amount_minor=dispute_amount,
        currency="INR",
        reason_code="fraud",
        phase="chargeback",
        status="open",
        dispute_created_at=dispute_created_at or datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc),
        source_updated_at=datetime.now(timezone.utc),
    )
    db.add(dispute)
    db.flush()

    return case, dispute


# =========================================================================
# 1. Successful complete enrichment
# =========================================================================

def test_complete_enrichment():
    """All entities found, persisted, state → ENRICHED."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_valid1")
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        assert result.status == "ENRICHED"
        assert result.payment_status == LookupStatus.FOUND
        assert result.order_status == LookupStatus.FOUND
        assert result.shipment_status == LookupStatus.FOUND
        assert result.refund_status == LookupStatus.FOUND
        assert result.customer_history_status == LookupStatus.FOUND
        assert result.version == 1

        # Verify DB persistence
        db.expire_all()
        case = db.query(Case).filter(Case.case_id == case.case_id).first()
        assert case.processing_state == ProcessingState.ENRICHED

        assert db.query(Payment).filter(Payment.case_id == case.case_id).count() == 1
        assert db.query(Order).filter(Order.case_id == case.case_id).count() == 1
        assert db.query(Shipment).filter(Shipment.case_id == case.case_id).count() == 1
        assert db.query(Refund).filter(Refund.case_id == case.case_id).count() == 1
        assert db.query(CustomerHistory).filter(CustomerHistory.case_id == case.case_id).count() == 1
        assert db.query(CaseEnrichment).filter(CaseEnrichment.case_id == case.case_id).count() == 1
    finally:
        db.close()


# =========================================================================
# 2. Payment not found → terminal failure
# =========================================================================

def test_payment_not_found():
    """Terminal failure: payment not found, case reverts to INGESTED, ProcessingError recorded."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_not_found")
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        assert result.status == "FAILED"
        assert result.payment_status == LookupStatus.NOT_FOUND

        db.expire_all()
        case = db.query(Case).filter(Case.case_id == case.case_id).first()
        # Correction #1: case reverts to INGESTED, not stuck in ENRICHING
        assert case.processing_state == ProcessingState.INGESTED

        errors = db.query(ProcessingError).filter(ProcessingError.case_id == case.case_id).all()
        assert len(errors) == 1
        assert errors[0].module == "module_b"
        assert errors[0].error_code == "PAYMENT_NOT_FOUND"
        assert errors[0].retryable is False

        # No enrichment entities should be persisted
        assert db.query(Payment).filter(Payment.case_id == case.case_id).count() == 0
    finally:
        db.close()


# =========================================================================
# 3. Order not found → enrichment completes with gap
# =========================================================================

def test_order_not_found():
    """Order not found: enrichment completes, order completeness = 0.0."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_no_order")
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        assert result.status == "ENRICHED"
        assert result.payment_status == LookupStatus.FOUND
        assert result.order_status == LookupStatus.NOT_AVAILABLE
        assert result.completeness.order == 0.0
        assert result.completeness.payment > 0.0

        db.expire_all()
        case = db.query(Case).filter(Case.case_id == case.case_id).first()
        assert case.processing_state == ProcessingState.ENRICHED
        assert db.query(Order).filter(Order.case_id == case.case_id).count() == 0
    finally:
        db.close()


# =========================================================================
# 4. Shipment unavailable
# =========================================================================

def test_shipment_unavailable():
    """Shipment not found: enrichment completes, shipment completeness = 0.0."""
    db = TestingSessionLocal()
    try:
        # Use a payment that has order "order_valid1" which maps to ship_not_found?
        # We need order to exist but shipment to not exist.
        # Create a provider with custom shipment provider that raises NotFound.
        class NoShipmentProvider(SyntheticShipmentProvider):
            def get_shipment(self, order_id):
                raise ProviderNotFoundError("shipment", order_id)

        providers = ProviderBundle(
            payment=SyntheticPaymentProvider(),
            order=SyntheticOrderProvider(),
            shipment=NoShipmentProvider(),
            refund=SyntheticRefundProvider(),
            customer_history=SyntheticCustomerHistoryProvider(),
        )

        case, dispute = _create_case_with_dispute(db, payment_id="pay_valid1")
        result = enrich_case(db, str(case.case_id), providers)

        assert result.status == "ENRICHED"
        assert result.shipment_status == LookupStatus.NOT_FOUND
        assert result.completeness.shipment == 0.0

        db.expire_all()
        assert db.query(Shipment).filter(Shipment.case_id == case.case_id).count() == 0
    finally:
        db.close()


# =========================================================================
# 5. No refund
# =========================================================================

def test_no_refund():
    """Explicitly no refunds: refund status = FOUND, completeness = 1.0."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_no_refund")
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        assert result.status == "ENRICHED"
        assert result.refund_status == LookupStatus.FOUND
        # Empty list = complete information about refunds (there are none)
        assert result.completeness.refund == 1.0

        db.expire_all()
        assert db.query(Refund).filter(Refund.case_id == case.case_id).count() == 0
    finally:
        db.close()


# =========================================================================
# 6. Partial refund
# =========================================================================

def test_partial_refund():
    """Partial refund: net amount correctly calculated."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_refund_partial")
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        assert result.status == "ENRICHED"
        assert result.refund_status == LookupStatus.FOUND

        db.expire_all()
        refunds = db.query(Refund).filter(Refund.case_id == case.case_id).all()
        assert len(refunds) == 1
        assert refunds[0].refund_amount_minor == 30000  # Partial

        # Payment amount is 100000, refund is 30000, net = 70000
        # Dispute amount is 100000 > 70000 → AMOUNT_INCONSISTENT
        assert "AMOUNT_INCONSISTENT" in result.consistency_flags
    finally:
        db.close()


# =========================================================================
# 7. Multiple refunds
# =========================================================================

def test_multiple_refunds():
    """Multiple refunds: all persisted, net amount correctly computed."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_refund_multi")
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        assert result.status == "ENRICHED"
        db.expire_all()
        refunds = db.query(Refund).filter(Refund.case_id == case.case_id).all()
        assert len(refunds) == 2

        total_refunded = sum(r.refund_amount_minor for r in refunds)
        assert total_refunded == 35000  # 20000 + 15000

        # Payment 100000, refunded 35000, net = 65000
        # Dispute 100000 > 65000 → AMOUNT_INCONSISTENT
        assert "AMOUNT_INCONSISTENT" in result.consistency_flags
    finally:
        db.close()


# =========================================================================
# 8. PAYMENT_MISMATCH
# =========================================================================

def test_payment_mismatch():
    """Payment provider returns mismatched external_payment_id."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_mismatch")
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        assert result.status == "ENRICHED"
        assert "PAYMENT_MISMATCH" in result.consistency_flags
    finally:
        db.close()


# =========================================================================
# 9. ORDER_MISMATCH
# =========================================================================

def test_order_mismatch():
    """Order provider returns mismatched external_order_id."""
    db = TestingSessionLocal()
    try:
        class MismatchOrderProvider(SyntheticOrderProvider):
            def get_order(self, order_id):
                data = super().get_order(order_id)
                data.external_order_id = "order_WRONG_ID"
                return data

        providers = ProviderBundle(
            payment=SyntheticPaymentProvider(),
            order=MismatchOrderProvider(),
            shipment=SyntheticShipmentProvider(),
            refund=SyntheticRefundProvider(),
            customer_history=SyntheticCustomerHistoryProvider(),
        )

        case, dispute = _create_case_with_dispute(db, payment_id="pay_valid1")
        result = enrich_case(db, str(case.case_id), providers)

        assert "ORDER_MISMATCH" in result.consistency_flags
    finally:
        db.close()


# =========================================================================
# 10. SHIPMENT_MISMATCH
# =========================================================================

def test_shipment_mismatch():
    """Shipment provider returns mismatched external_order_id."""
    db = TestingSessionLocal()
    try:
        class MismatchShipProvider(SyntheticShipmentProvider):
            def get_shipment(self, order_id):
                data = super().get_shipment(order_id)
                data.external_order_id = "order_WRONG_SHIP"
                return data

        providers = ProviderBundle(
            payment=SyntheticPaymentProvider(),
            order=SyntheticOrderProvider(),
            shipment=MismatchShipProvider(),
            refund=SyntheticRefundProvider(),
            customer_history=SyntheticCustomerHistoryProvider(),
        )

        case, dispute = _create_case_with_dispute(db, payment_id="pay_valid1")
        result = enrich_case(db, str(case.case_id), providers)

        assert "SHIPMENT_MISMATCH" in result.consistency_flags
    finally:
        db.close()


# =========================================================================
# 11. REFUND_MISMATCH
# =========================================================================

def test_refund_mismatch():
    """Refund provider returns mismatched external_payment_id."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_refund_mismatch")
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        assert "REFUND_MISMATCH" in result.consistency_flags
    finally:
        db.close()


# =========================================================================
# 12. AMOUNT_INCONSISTENT
# =========================================================================

def test_amount_inconsistent():
    """Disputed amount > net payment amount → AMOUNT_INCONSISTENT."""
    db = TestingSessionLocal()
    try:
        # pay_amount_high returns amount=50000. Dispute amount=100000 > 50000.
        case, dispute = _create_case_with_dispute(db, payment_id="pay_amount_high")
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        assert "AMOUNT_INCONSISTENT" in result.consistency_flags
    finally:
        db.close()


# =========================================================================
# 13. TIMELINE_INCONSISTENT
# =========================================================================

def test_timeline_inconsistent():
    """Shipment dispatch after delivery → TIMELINE_INCONSISTENT."""
    db = TestingSessionLocal()
    try:
        class BadTimelineShipProvider(SyntheticShipmentProvider):
            def get_shipment(self, order_id):
                data = super().get_shipment(order_id)
                # dispatch AFTER delivery
                data.dispatch_at = datetime(2026, 8, 10, 0, 0, 0, tzinfo=timezone.utc)
                data.delivery_at = datetime(2026, 8, 3, 0, 0, 0, tzinfo=timezone.utc)
                return data

        providers = ProviderBundle(
            payment=SyntheticPaymentProvider(),
            order=SyntheticOrderProvider(),
            shipment=BadTimelineShipProvider(),
            refund=SyntheticRefundProvider(),
            customer_history=SyntheticCustomerHistoryProvider(),
        )

        case, dispute = _create_case_with_dispute(db, payment_id="pay_valid1")
        result = enrich_case(db, str(case.case_id), providers)

        assert "TIMELINE_INCONSISTENT" in result.consistency_flags
    finally:
        db.close()


# =========================================================================
# 14. Correct derived timeline intervals
# =========================================================================

def test_timeline_intervals():
    """Verify derived timeline interval calculations."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(
            db, payment_id="pay_valid1",
            dispute_created_at=datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc),
        )
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        t = result.timeline
        assert t is not None

        # Synthetic data: order=Aug 1 10:00, payment=Aug 1 11:00, dispatch=Aug 1 12:00, delivery=Aug 4 10:00
        # Dispute=Aug 10 12:00

        # order_to_payment_minutes: 60 minutes
        assert t.order_to_payment_minutes == 60.0

        # payment_to_dispatch_hours: 1 hour
        assert t.payment_to_dispatch_hours == 1.0

        # dispatch_to_delivery_hours: Aug 1 12:00 → Aug 4 10:00 = 70 hours
        assert t.dispatch_to_delivery_hours == 70.0

        # delivery_to_dispute_days: Aug 4 10:00 → Aug 10 12:00 = 6.0833.. days
        assert t.delivery_to_dispute_days is not None
        assert t.delivery_to_dispute_days > 6.0
    finally:
        db.close()


# =========================================================================
# 15. Completeness calculation
# =========================================================================

def test_completeness_calculation():
    """Per-entity and overall completeness values are correct and bounded."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_valid1")
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        c = result.completeness
        assert c is not None
        assert 0.0 < c.payment <= 1.0
        assert 0.0 < c.order <= 1.0
        assert 0.0 < c.shipment <= 1.0
        assert 0.0 < c.refund <= 1.0
        assert 0.0 < c.customer_history <= 1.0
        assert 0.0 < c.overall <= 1.0

        # Verify persisted in CaseEnrichment
        db.expire_all()
        enrichment = db.query(CaseEnrichment).filter(
            CaseEnrichment.case_id == case.case_id
        ).first()
        assert enrichment is not None
        assert float(enrichment.overall_complete) > 0
        assert float(enrichment.payment_complete) > 0
    finally:
        db.close()


# =========================================================================
# 16. State: INGESTED → ENRICHING → ENRICHED
# =========================================================================

def test_state_progression():
    """Case transitions correctly through INGESTED → ENRICHING → ENRICHED."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_valid1")
        assert case.processing_state == ProcessingState.INGESTED

        providers = get_synthetic_providers()
        result = enrich_case(db, str(case.case_id), providers)

        assert result.status == "ENRICHED"

        db.expire_all()
        case = db.query(Case).filter(Case.case_id == case.case_id).first()
        assert case.processing_state == ProcessingState.ENRICHED
    finally:
        db.close()


# =========================================================================
# 17. Transient provider failure retries
# =========================================================================

def test_transient_provider_retry():
    """ProviderUnavailableError bubbles up for Celery retry."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_unavailable")
        providers = get_synthetic_providers()

        with pytest.raises(ProviderUnavailableError):
            enrich_case(db, str(case.case_id), providers)

        # Case should be in ENRICHING (in-progress, waiting for retry)
        db.expire_all()
        case = db.query(Case).filter(Case.case_id == case.case_id).first()
        assert case.processing_state == ProcessingState.ENRICHING
    finally:
        db.close()


# =========================================================================
# 18. Terminal provider failure
# =========================================================================

def test_terminal_failure_records_error():
    """Payment NOT_FOUND is terminal: error recorded, case → INGESTED."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_not_found")
        providers = get_synthetic_providers()

        result = enrich_case(db, str(case.case_id), providers)

        assert result.status == "FAILED"

        db.expire_all()
        case = db.query(Case).filter(Case.case_id == case.case_id).first()
        assert case.processing_state == ProcessingState.INGESTED

        errors = db.query(ProcessingError).all()
        assert len(errors) == 1
        assert errors[0].retryable is False
    finally:
        db.close()


# =========================================================================
# 19. ProcessingError creation
# =========================================================================

def test_processing_error_fields():
    """ProcessingError has correct module, error_code, retryable."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_not_found")
        providers = get_synthetic_providers()

        enrich_case(db, str(case.case_id), providers)

        db.expire_all()
        error = db.query(ProcessingError).first()
        assert error is not None
        assert error.module == "module_b"
        assert error.error_code == "PAYMENT_NOT_FOUND"
        assert error.case_id == case.case_id
        assert error.retryable is False
        assert "pay_not_found" in error.error_message
    finally:
        db.close()


# =========================================================================
# 20. Manual enrichment endpoint
# =========================================================================

def test_manual_enrichment_endpoint(mocker):
    """POST /api/v1/cases/{case_id}/enrich returns 202."""
    mock_delay = mocker.patch("app.api.endpoints.enrichment.enrich_dispute_task.delay")

    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_valid1")
        case_id = str(case.case_id)
        db.commit()
    finally:
        db.close()

    response = client.post(f"/api/v1/cases/{case_id}/enrich")
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["case_id"] == case_id
    mock_delay.assert_called_once_with(case_id)


# =========================================================================
# 21. Duplicate/concurrent enrichment prevention
# =========================================================================

def test_concurrent_enrichment_prevention(mocker):
    """Case in ENRICHING state → 409 Conflict on manual enrich."""
    mocker.patch("app.api.endpoints.enrichment.enrich_dispute_task.delay")

    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(
            db, payment_id="pay_valid1", state=ProcessingState.ENRICHING,
        )
        case_id = str(case.case_id)
        db.commit()
    finally:
        db.close()

    response = client.post(f"/api/v1/cases/{case_id}/enrich")
    assert response.status_code == 409


# =========================================================================
# 22. Module A remains unaffected (integration test)
# =========================================================================

def test_module_a_ingestion_still_works(mocker):
    """Verify Module A webhook ingestion works after Module B changes."""
    mock_delay = mocker.patch("app.services.ingestion.enrich_dispute_task.delay")

    payload = {
        "entity": "event",
        "account_id": "acc_123",
        "event": "dispute.created",
        "contains": ["dispute"],
        "payload": {
            "dispute": {
                "entity": {
                    "id": "disp_regr_b",
                    "payment_id": "pay_regr_b",
                    "amount": 1000,
                    "currency": "INR",
                    "reason_code": "fraud",
                    "reason_description": "fraud desc",
                    "phase": "fraud",
                    "status": "open",
                    "created_at": int(time.time()),
                }
            }
        },
        "created_at": int(time.time()),
    }
    payload_str = json.dumps(payload)
    sig = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-Razorpay-Signature": sig,
        "X-Razorpay-Event-Id": "evt_regr_b",
    }

    response = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert response.status_code == 202

    db = TestingSessionLocal()
    try:
        case = db.query(Case).first()
        assert case is not None
        assert case.processing_state == ProcessingState.INGESTED

        disp = db.query(Dispute).filter(Dispute.case_id == case.case_id).first()
        assert disp is not None
        assert disp.external_dispute_id == "disp_regr_b"

        mock_delay.assert_called_once()
    finally:
        db.close()


# =========================================================================
# 23. Module A test suite passes (sanity check — run test_module_a separately)
# =========================================================================

def test_module_a_dev_endpoint_still_works(mocker):
    """Module A dev endpoint remains functional after Module B implementation."""
    mock_delay = mocker.patch("app.services.ingestion.enrich_dispute_task.delay")

    settings.ENABLE_DEV_ENDPOINTS = True

    payload = {
        "external_event_id": "dev_regr_b",
        "event_type": "dispute.created",
        "external_dispute_id": "disp_dev_regr",
        "payment_id": "pay_dev_regr",
        "amount_minor": 500,
        "currency": "INR",
        "reason_code": "fraud",
        "status": "open",
        "phase": "fraud",
        "dispute_created_at": datetime.now(timezone.utc).isoformat(),
        "event_time": datetime.now(timezone.utc).isoformat(),
    }

    r = client.post("/api/v1/dev/disputes", json=payload)
    assert r.status_code == 202
    mock_delay.assert_called_once()


# =========================================================================
# Additional edge cases
# =========================================================================

def test_re_enrichment_increments_version():
    """Re-enrichment of an already ENRICHED case creates version 2."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_valid1")
        providers = get_synthetic_providers()

        # First enrichment
        r1 = enrich_case(db, str(case.case_id), providers)
        assert r1.version == 1

        # Re-enrich (case is now ENRICHED)
        r2 = enrich_case(db, str(case.case_id), providers)
        assert r2.version == 2

        db.expire_all()
        enrichments = db.query(CaseEnrichment).filter(
            CaseEnrichment.case_id == case.case_id
        ).order_by(CaseEnrichment.version).all()
        assert len(enrichments) == 2
        assert enrichments[0].version == 1
        assert enrichments[1].version == 2
    finally:
        db.close()


def test_audit_log_created_on_enrichment():
    """Audit log entry persisted for enrichment."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_valid1")
        providers = get_synthetic_providers()

        enrich_case(db, str(case.case_id), providers)

        db.expire_all()
        audits = db.query(AuditLog).filter(
            AuditLog.case_id == case.case_id,
            AuditLog.action == "CASE_ENRICHMENT_COMPLETED",
        ).all()
        assert len(audits) == 1
        assert "payment=FOUND" in audits[0].details
    finally:
        db.close()


def test_enrichment_endpoint_invalid_case_id(mocker):
    """Invalid case_id returns 400."""
    mocker.patch("app.api.endpoints.enrichment.enrich_dispute_task.delay")
    response = client.post("/api/v1/cases/not-a-uuid/enrich")
    assert response.status_code == 400


def test_enrichment_endpoint_case_not_found(mocker):
    """Non-existent case returns 404."""
    mocker.patch("app.api.endpoints.enrichment.enrich_dispute_task.delay")
    fake_id = str(uuid.uuid4())
    response = client.post(f"/api/v1/cases/{fake_id}/enrich")
    assert response.status_code == 404


def test_re_enrichment_preserves_data_on_persistence_failure():
    """Verify that if re-enrichment fails during persistence, old data is kept."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_valid1")
        providers = get_synthetic_providers()

        # First enrichment succeeds
        r1 = enrich_case(db, str(case.case_id), providers)
        assert r1.version == 1

        db.expire_all()
        # Verify old data exists
        assert db.query(Payment).filter(Payment.case_id == case.case_id).count() == 1

        # Simulate persistence failure in second enrichment by passing bad provider
        # which will raise an error after deletion in the same transaction.
        # We can do this by making the session raise an exception on add.
        with patch.object(db, "add", side_effect=Exception("Simulated DB persistence failure")):
            try:
                enrich_case(db, str(case.case_id), providers)
            except Exception as e:
                db.rollback()  # mimic tasks.py behaviour
                assert str(e) == "Simulated DB persistence failure"

        # Verify old data STILL exists after rollback
        db.expire_all()
        assert db.query(Payment).filter(Payment.case_id == case.case_id).count() == 1
        assert db.query(CaseEnrichment).filter(CaseEnrichment.case_id == case.case_id).count() == 1
    finally:
        db.close()


# =========================================================================
# 24. Module B network extension tests
# =========================================================================

def test_payment_data_accepts_network():
    """PaymentData schema accepts network and allows it to be None."""
    from app.schemas.module_b import PaymentData
    
    # Accept network
    p1 = PaymentData(
        external_payment_id="test1",
        amount_minor=100,
        currency="INR",
        status="cap",
        method="card",
        network="Visa"
    )
    assert p1.network == "Visa"
    
    # Allows None
    p2 = PaymentData(
        external_payment_id="test2",
        amount_minor=100,
        currency="INR",
        status="cap",
        method="upi"
    )
    assert p2.network is None

def test_synthetic_provider_network_mapping():
    """Synthetic provider deterministically maps network."""
    provider = SyntheticPaymentProvider()
    
    # Standard valid pay
    p1 = provider.get_payment("pay_valid1")
    assert p1.method == "card"
    assert p1.network == "Visa"
    
    # Mismatch pay
    p2 = provider.get_payment("pay_mismatch")
    assert p2.method == "card"
    assert p2.network == "Mastercard"
    
    # Non-card pay
    p3 = provider.get_payment("pay_non_card")
    assert p3.method == "upi"
    assert p3.network is None
    
    # Card without network
    p4 = provider.get_payment("pay_no_network")
    assert p4.method == "card"
    assert p4.network is None
    
def test_enrichment_persists_network():
    """Enrichment persists Payment.network from PaymentData."""
    db = TestingSessionLocal()
    try:
        case, dispute = _create_case_with_dispute(db, payment_id="pay_mismatch")
        providers = get_synthetic_providers()
        
        enrich_case(db, str(case.case_id), providers)
        
        db.expire_all()
        payment = db.query(Payment).filter(Payment.case_id == case.case_id).first()
        assert payment is not None
        assert payment.method == "card"
        assert payment.network == "Mastercard"
        
        # Regression: method != network
        assert payment.method != payment.network
    finally:
        db.close()
