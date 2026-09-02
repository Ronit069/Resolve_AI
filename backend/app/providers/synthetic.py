"""
Deterministic synthetic provider implementations.

These providers return canonical data structures for development and testing.
Data is deterministic based on input IDs.

Magic IDs for edge-case testing:
  Payment:
    - "pay_not_found"       → ProviderNotFoundError
    - "pay_unavailable"     → ProviderUnavailableError
    - "pay_mismatch"        → returns payment with mismatched payment_id
    - "pay_amount_high"     → returns payment with amount lower than dispute
    - "pay_no_order"        → returns payment without external_order_id
    - any other             → deterministic valid payment

  Order:
    - "order_not_found"     → ProviderNotFoundError
    - "order_unavailable"   → ProviderUnavailableError
    - "order_mismatch"      → returns order with mismatched order_id
    - any other             → deterministic valid order

  Shipment:
    - "ship_not_found"      → ProviderNotFoundError (no shipment for this order)
    - "ship_unavailable"    → ProviderUnavailableError
    - "ship_mismatch"       → returns shipment with mismatched order_id
    - "ship_timeline_bad"   → returns shipment with dispatch after delivery
    - any other             → deterministic valid shipment

  Refund:
    - "pay_no_refund"       → empty list (no refunds, not an error)
    - "pay_refund_partial"  → one partial refund
    - "pay_refund_multi"    → multiple refunds
    - "pay_refund_mismatch" → refund with mismatched payment_id
    - "pay_unavailable"     → ProviderUnavailableError
    - any other             → one full refund

  Customer History:
    - "cust_not_found"      → ProviderNotFoundError
    - "cust_unavailable"    → ProviderUnavailableError
    - any other             → deterministic history
"""

import hashlib
from datetime import datetime, timezone, timedelta
from typing import List

from app.providers.base import (
    PaymentProvider,
    OrderProvider,
    ShipmentProvider,
    RefundProvider,
    CustomerHistoryProvider,
    ProviderNotFoundError,
    ProviderUnavailableError,
    ProviderBundle,
)
from app.schemas.module_b import (
    PaymentData,
    OrderData,
    ShipmentData,
    RefundData,
    CustomerHistoryData,
)


# ---------------------------------------------------------------------------
# Base timestamp for deterministic data
# ---------------------------------------------------------------------------
_BASE_TIME = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


def _hash(value: str) -> str:
    """Deterministic SHA-256 hash for privacy-safe references."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Synthetic Payment Provider
# ---------------------------------------------------------------------------

class SyntheticPaymentProvider(PaymentProvider):
    def get_payment(self, payment_id: str) -> PaymentData:
        if payment_id == "pay_not_found":
            raise ProviderNotFoundError("payment", payment_id)
        if payment_id == "pay_unavailable":
            raise ProviderUnavailableError("payment", payment_id)

        # Determine order_id
        external_order_id = None
        if payment_id != "pay_no_order":
            external_order_id = f"order_{payment_id.replace('pay_', '')}"

        # Determine amount
        amount = 100000  # 1000.00 in minor units (paise)
        if payment_id == "pay_amount_high":
            amount = 50000  # Lower than typical dispute amount → triggers AMOUNT_INCONSISTENT

        # Mismatch: return a different payment_id than requested
        returned_payment_id = payment_id
        if payment_id == "pay_mismatch":
            returned_payment_id = "pay_WRONG_ID"

        # Deterministic method and network
        method = "card"
        network = "Visa"
        
        if payment_id == "pay_mismatch":
            network = "Mastercard"
        elif payment_id == "pay_non_card":
            method = "upi"
            network = None
        elif payment_id == "pay_no_network":
            method = "card"
            network = None

        return PaymentData(
            external_payment_id=returned_payment_id,
            external_order_id=external_order_id,
            amount_minor=amount,
            currency="INR",
            status="captured",
            method=method,
            network=network,
            captured=True,
            created_at_source=_BASE_TIME + timedelta(hours=1),
        )


# ---------------------------------------------------------------------------
# Synthetic Order Provider
# ---------------------------------------------------------------------------

class SyntheticOrderProvider(OrderProvider):
    def get_order(self, order_id: str) -> OrderData:
        if order_id == "order_not_found":
            raise ProviderNotFoundError("order", order_id)
        if order_id == "order_unavailable":
            raise ProviderUnavailableError("order", order_id)

        returned_order_id = order_id
        if order_id == "order_mismatch":
            returned_order_id = "order_WRONG_ID"

        return OrderData(
            external_order_id=returned_order_id,
            merchant_order_ref=f"MREF-{order_id}",
            order_amount_minor=100000,
            currency="INR",
            order_status="fulfilled",
            customer_ref_hash=_hash(f"customer_{order_id}"),
            product_description="Premium Widget",
            quantity=1,
            created_at_source=_BASE_TIME,  # Order before payment
        )


# ---------------------------------------------------------------------------
# Synthetic Shipment Provider
# ---------------------------------------------------------------------------

class SyntheticShipmentProvider(ShipmentProvider):
    def get_shipment(self, order_id: str) -> ShipmentData:
        if order_id == "order_not_found" or order_id == "ship_not_found":
            raise ProviderNotFoundError("shipment", order_id)
        if order_id == "ship_unavailable":
            raise ProviderUnavailableError("shipment", order_id)

        returned_order_id = order_id
        if order_id == "ship_mismatch":
            returned_order_id = "order_WRONG_SHIP"

        dispatch_at = _BASE_TIME + timedelta(hours=2)
        delivery_at = _BASE_TIME + timedelta(days=3)

        if order_id == "ship_timeline_bad":
            # dispatch AFTER delivery — triggers TIMELINE_INCONSISTENT
            dispatch_at = _BASE_TIME + timedelta(days=5)
            delivery_at = _BASE_TIME + timedelta(days=1)

        return ShipmentData(
            shipment_id=f"shp_{order_id}",
            external_order_id=returned_order_id,
            courier="FastShip Express",
            tracking_id=f"TRK-{order_id}",
            dispatch_at=dispatch_at,
            delivery_at=delivery_at,
            delivery_status="delivered",
            delivery_address_hash=_hash(f"addr_{order_id}"),
            recipient_confirmation=True,
        )


# ---------------------------------------------------------------------------
# Synthetic Refund Provider
# ---------------------------------------------------------------------------

class SyntheticRefundProvider(RefundProvider):
    def get_refunds(self, payment_id: str) -> List[RefundData]:
        if payment_id == "pay_unavailable":
            raise ProviderUnavailableError("refund", payment_id)

        if payment_id == "pay_no_refund":
            return []  # Explicitly no refunds

        if payment_id == "pay_refund_partial":
            return [
                RefundData(
                    external_refund_id=f"rfnd_partial_{payment_id}",
                    external_payment_id=payment_id,
                    refund_amount_minor=30000,  # Partial: 300.00 of 1000.00
                    status="processed",
                    refund_reason="customer_request",
                    refund_at=_BASE_TIME + timedelta(days=5),
                )
            ]

        if payment_id == "pay_refund_multi":
            return [
                RefundData(
                    external_refund_id=f"rfnd_1_{payment_id}",
                    external_payment_id=payment_id,
                    refund_amount_minor=20000,
                    status="processed",
                    refund_reason="partial_return",
                    refund_at=_BASE_TIME + timedelta(days=4),
                ),
                RefundData(
                    external_refund_id=f"rfnd_2_{payment_id}",
                    external_payment_id=payment_id,
                    refund_amount_minor=15000,
                    status="processed",
                    refund_reason="price_adjustment",
                    refund_at=_BASE_TIME + timedelta(days=6),
                ),
            ]

        if payment_id == "pay_refund_mismatch":
            return [
                RefundData(
                    external_refund_id=f"rfnd_{payment_id}",
                    external_payment_id="pay_WRONG_REFUND",  # Mismatched
                    refund_amount_minor=50000,
                    status="processed",
                    refund_reason="fraud_claim",
                    refund_at=_BASE_TIME + timedelta(days=5),
                )
            ]

        # Default: one full refund
        return [
            RefundData(
                external_refund_id=f"rfnd_{payment_id}",
                external_payment_id=payment_id,
                refund_amount_minor=100000,
                status="processed",
                refund_reason="dispute_resolution",
                refund_at=_BASE_TIME + timedelta(days=5),
            )
        ]


# ---------------------------------------------------------------------------
# Synthetic Customer History Provider
# ---------------------------------------------------------------------------

class SyntheticCustomerHistoryProvider(CustomerHistoryProvider):
    def get_customer_history(self, customer_ref_hash: str) -> CustomerHistoryData:
        if customer_ref_hash == "cust_not_found":
            raise ProviderNotFoundError("customer_history", customer_ref_hash)
        if customer_ref_hash == "cust_unavailable":
            raise ProviderUnavailableError("customer_history", customer_ref_hash)

        return CustomerHistoryData(
            customer_ref_hash=customer_ref_hash,
            account_age_days=365,
            previous_order_count=12,
            previous_dispute_count=1,
            previous_refund_count=2,
            refund_rate=0.16667,
            dispute_rate=0.08333,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_synthetic_providers() -> ProviderBundle:
    """Create a ProviderBundle with all synthetic implementations."""
    return ProviderBundle(
        payment=SyntheticPaymentProvider(),
        order=SyntheticOrderProvider(),
        shipment=SyntheticShipmentProvider(),
        refund=SyntheticRefundProvider(),
        customer_history=SyntheticCustomerHistoryProvider(),
    )
