"""
Module B — Enrichment Service.

Core orchestration for case enrichment: loads dispute, retrieves data from
provider adapters, validates relationships, builds timeline, calculates
completeness, and persists results transactionally.

Corrections incorporated:
  1. Terminal failure → revert case to INGESTED (not stuck in ENRICHING).
  2. Safe re-enrichment → retrieve fresh data first; only replace entity
     rows after successful retrieval and validation.
  4. Payment = mandatory critical enrichment for ENRICHED transition.
  5. No silent data fabrication — missing entities are explicit gaps.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.module_a import Dispute
from app.models.module_b import (
    Payment, Order, Shipment, Refund, CustomerHistory, CaseEnrichment,
)
from app.models.shared import Case, ProcessingState, AuditLog, ProcessingError
from app.providers.base import (
    ProviderBundle,
    ProviderNotFoundError,
    ProviderUnavailableError,
)
from app.schemas.module_b import (
    PaymentData, OrderData, ShipmentData, RefundData, CustomerHistoryData,
    LookupStatus, ConsistencyFlag, TimelineData, CompletenessData,
    EnrichmentResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Completeness calculation helpers
# ---------------------------------------------------------------------------

def _payment_completeness(data: Optional[PaymentData]) -> float:
    if data is None:
        return 0.0
    fields = [
        data.external_payment_id, data.amount_minor, data.currency,
        data.status, data.method, data.captured, data.created_at_source,
        data.external_order_id,
    ]
    filled = sum(1 for f in fields if f is not None)
    return round(filled / len(fields), 4)


def _order_completeness(data: Optional[OrderData]) -> float:
    if data is None:
        return 0.0
    fields = [
        data.external_order_id, data.merchant_order_ref,
        data.order_amount_minor, data.currency, data.order_status,
        data.customer_ref_hash, data.product_description, data.quantity,
        data.created_at_source,
    ]
    filled = sum(1 for f in fields if f is not None)
    return round(filled / len(fields), 4)


def _shipment_completeness(data: Optional[ShipmentData]) -> float:
    if data is None:
        return 0.0
    fields = [
        data.shipment_id, data.external_order_id, data.courier,
        data.tracking_id, data.dispatch_at, data.delivery_at,
        data.delivery_status, data.delivery_address_hash,
        data.recipient_confirmation,
    ]
    filled = sum(1 for f in fields if f is not None)
    return round(filled / len(fields), 4)


def _refund_completeness(refunds: List[RefundData]) -> float:
    """For refunds, completeness is 1.0 if lookup succeeded (even if empty list).
    NOT_FOUND/LOOKUP_FAILED gives 0.0 from the caller."""
    if not refunds:
        return 1.0  # Empty list = explicitly no refunds = complete information
    total_fields = 0
    filled_fields = 0
    for r in refunds:
        fields = [
            r.external_refund_id, r.external_payment_id,
            r.refund_amount_minor, r.status, r.refund_reason, r.refund_at,
        ]
        total_fields += len(fields)
        filled_fields += sum(1 for f in fields if f is not None)
    return round(filled_fields / total_fields, 4) if total_fields > 0 else 1.0


def _customer_history_completeness(data: Optional[CustomerHistoryData]) -> float:
    if data is None:
        return 0.0
    fields = [
        data.customer_ref_hash, data.account_age_days,
        data.previous_order_count, data.previous_dispute_count,
        data.previous_refund_count, data.refund_rate, data.dispute_rate,
    ]
    filled = sum(1 for f in fields if f is not None)
    return round(filled / len(fields), 4)


# ---------------------------------------------------------------------------
# Relationship validation
# ---------------------------------------------------------------------------

def _validate_relationships(
    dispute: Dispute,
    payment_data: Optional[PaymentData],
    order_data: Optional[OrderData],
    shipment_data: Optional[ShipmentData],
    refund_list: List[RefundData],
) -> List[str]:
    """Return list of consistency flag strings."""
    flags: List[str] = []

    if payment_data:
        # Dispute ↔ Payment
        if dispute.payment_id != payment_data.external_payment_id:
            flags.append(ConsistencyFlag.PAYMENT_MISMATCH)

        # Payment ↔ Order
        if order_data and payment_data.external_order_id:
            if payment_data.external_order_id != order_data.external_order_id:
                flags.append(ConsistencyFlag.ORDER_MISMATCH)

        # Order ↔ Shipment
        if order_data and shipment_data:
            if shipment_data.external_order_id != order_data.external_order_id:
                flags.append(ConsistencyFlag.SHIPMENT_MISMATCH)

        # Payment ↔ Refund
        for refund in refund_list:
            if refund.external_payment_id != payment_data.external_payment_id:
                flags.append(ConsistencyFlag.REFUND_MISMATCH)
                break  # One flag is enough

        # Financial consistency
        total_refunded = sum(r.refund_amount_minor for r in refund_list)
        net_amount = payment_data.amount_minor - total_refunded
        if dispute.amount_minor > net_amount:
            flags.append(ConsistencyFlag.AMOUNT_INCONSISTENT)

    return flags


# ---------------------------------------------------------------------------
# Timeline construction and validation
# ---------------------------------------------------------------------------

def _build_timeline(
    dispute: Dispute,
    payment_data: Optional[PaymentData],
    order_data: Optional[OrderData],
    shipment_data: Optional[ShipmentData],
) -> Tuple[TimelineData, bool]:
    """Build canonical timeline. Returns (timeline, is_consistent)."""
    order_at = order_data.created_at_source if order_data else None
    payment_at = payment_data.created_at_source if payment_data else None
    dispatch_at = shipment_data.dispatch_at if shipment_data else None
    delivery_at = shipment_data.delivery_at if shipment_data else None
    dispute_at = dispute.dispute_created_at
    if dispute_at and dispute_at.tzinfo is None:
        dispute_at = dispute_at.replace(tzinfo=timezone.utc)

    timeline = TimelineData(
        order_at=order_at,
        payment_at=payment_at,
        dispatch_at=dispatch_at,
        delivery_at=delivery_at,
        dispute_at=dispute_at,
    )

    # Calculate derived intervals (only when both endpoints exist)
    if order_at and payment_at:
        delta = (payment_at - order_at).total_seconds()
        timeline.order_to_payment_minutes = round(delta / 60, 2)

    if payment_at and dispatch_at:
        delta = (dispatch_at - payment_at).total_seconds()
        timeline.payment_to_dispatch_hours = round(delta / 3600, 2)

    if dispatch_at and delivery_at:
        delta = (delivery_at - dispatch_at).total_seconds()
        timeline.dispatch_to_delivery_hours = round(delta / 3600, 2)

    if delivery_at and dispute_at:
        delta = (dispute_at - delivery_at).total_seconds()
        timeline.delivery_to_dispute_days = round(delta / 86400, 2)

    # Validate temporal ordering: order ≤ payment ≤ dispatch ≤ delivery ≤ dispute
    is_consistent = True
    timestamps = [
        ("order", order_at),
        ("payment", payment_at),
        ("dispatch", dispatch_at),
        ("delivery", delivery_at),
        ("dispute", dispute_at),
    ]
    # Filter to only available timestamps and check ordering
    available = [(name, ts) for name, ts in timestamps if ts is not None]
    for i in range(len(available) - 1):
        if available[i][1] > available[i + 1][1]:
            is_consistent = False
            break

    return timeline, is_consistent


# ---------------------------------------------------------------------------
# Core enrichment function
# ---------------------------------------------------------------------------

def enrich_case(
    db: Session,
    case_id: str,
    providers: ProviderBundle,
) -> EnrichmentResult:
    """
    Orchestrate full case enrichment.

    Steps:
      1. Load case + dispute, validate state.
      2. Transition to ENRICHING.
      3. Retrieve all entities via providers (failures handled per-entity).
      4. Validate relationships, build timeline, calculate completeness.
      5. Persist results transactionally (safe re-enrichment).
      6. Transition to ENRICHED if payment found; else revert to INGESTED.
    """
    import uuid as uuid_mod

    # -----------------------------------------------------------------------
    # 1. Load and validate
    # -----------------------------------------------------------------------
    case = db.query(Case).filter(Case.case_id == uuid_mod.UUID(case_id)).first()
    if not case:
        raise ValueError(f"Case {case_id} not found")

    dispute = db.query(Dispute).filter(Dispute.case_id == case.case_id).first()
    if not dispute:
        raise ValueError(f"No dispute found for case {case_id}")

    # State guard: must be INGESTED or ENRICHING (for re-enrichment/retry)
    if case.processing_state not in (
        ProcessingState.INGESTED,
        ProcessingState.ENRICHING,
        ProcessingState.ENRICHED,  # Allow re-enrichment of previously enriched cases
    ):
        raise ValueError(
            f"Case {case_id} in state {case.processing_state.value}, "
            f"cannot enrich"
        )

    # -----------------------------------------------------------------------
    # 2. Transition to ENRICHING
    # -----------------------------------------------------------------------
    case.processing_state = ProcessingState.ENRICHING
    db.flush()

    # -----------------------------------------------------------------------
    # 3. Retrieve entities (per-entity failure handling)
    # -----------------------------------------------------------------------
    payment_data: Optional[PaymentData] = None
    payment_status = LookupStatus.NOT_AVAILABLE
    order_data: Optional[OrderData] = None
    order_status = LookupStatus.NOT_AVAILABLE
    shipment_data: Optional[ShipmentData] = None
    shipment_status = LookupStatus.NOT_AVAILABLE
    refund_list: List[RefundData] = []
    refund_status = LookupStatus.NOT_AVAILABLE
    customer_data: Optional[CustomerHistoryData] = None
    customer_status = LookupStatus.NOT_AVAILABLE

    # --- Payment (CRITICAL — must succeed for ENRICHED) ---
    try:
        payment_data = providers.payment.get_payment(dispute.payment_id)
        payment_status = LookupStatus.FOUND
    except ProviderNotFoundError:
        payment_status = LookupStatus.NOT_FOUND
        logger.warning(f"Payment not found for case {case_id}: {dispute.payment_id}")
    except ProviderUnavailableError:
        payment_status = LookupStatus.LOOKUP_FAILED
        logger.error(f"Payment provider unavailable for case {case_id}")
        raise  # Bubble up for Celery retry

    # --- Order (optional) ---
    if payment_data and payment_data.external_order_id:
        try:
            order_data = providers.order.get_order(payment_data.external_order_id)
            order_status = LookupStatus.FOUND
        except ProviderNotFoundError:
            order_status = LookupStatus.NOT_FOUND
            logger.info(f"Order not found for case {case_id}: {payment_data.external_order_id}")
        except ProviderUnavailableError:
            order_status = LookupStatus.LOOKUP_FAILED
            logger.warning(f"Order provider unavailable for case {case_id}")
    elif payment_data and not payment_data.external_order_id:
        order_status = LookupStatus.NOT_AVAILABLE

    # --- Shipment (optional) ---
    if order_data:
        try:
            shipment_data = providers.shipment.get_shipment(order_data.external_order_id)
            shipment_status = LookupStatus.FOUND
        except ProviderNotFoundError:
            shipment_status = LookupStatus.NOT_FOUND
            logger.info(f"Shipment not found for case {case_id}")
        except ProviderUnavailableError:
            shipment_status = LookupStatus.LOOKUP_FAILED
            logger.warning(f"Shipment provider unavailable for case {case_id}")

    # --- Refunds (optional, zero or more) ---
    if payment_data:
        try:
            refund_list = providers.refund.get_refunds(payment_data.external_payment_id)
            refund_status = LookupStatus.FOUND
        except ProviderNotFoundError:
            refund_status = LookupStatus.NOT_FOUND
            logger.info(f"Refund lookup: payment unknown for case {case_id}")
        except ProviderUnavailableError:
            refund_status = LookupStatus.LOOKUP_FAILED
            logger.warning(f"Refund provider unavailable for case {case_id}")

    # --- Customer History (optional) ---
    customer_ref = order_data.customer_ref_hash if order_data else None
    if customer_ref:
        try:
            customer_data = providers.customer_history.get_customer_history(customer_ref)
            customer_status = LookupStatus.FOUND
        except ProviderNotFoundError:
            customer_status = LookupStatus.NOT_FOUND
            logger.info(f"Customer history not found for case {case_id}")
        except ProviderUnavailableError:
            customer_status = LookupStatus.LOOKUP_FAILED
            logger.warning(f"Customer history provider unavailable for case {case_id}")

    # -----------------------------------------------------------------------
    # 4. Validate relationships, build timeline, calculate completeness
    # -----------------------------------------------------------------------
    consistency_flags = _validate_relationships(
        dispute, payment_data, order_data, shipment_data, refund_list,
    )

    timeline, timeline_consistent = _build_timeline(
        dispute, payment_data, order_data, shipment_data,
    )
    if not timeline_consistent:
        consistency_flags.append(ConsistencyFlag.TIMELINE_INCONSISTENT)

    # Completeness
    comp = CompletenessData(
        payment=_payment_completeness(payment_data),
        order=_order_completeness(order_data),
        shipment=_shipment_completeness(shipment_data),
        refund=_refund_completeness(refund_list) if refund_status == LookupStatus.FOUND else 0.0,
        customer_history=_customer_history_completeness(customer_data),
    )
    # Overall: weighted average (payment weighted higher as critical)
    weights = {"payment": 0.30, "order": 0.20, "shipment": 0.15, "refund": 0.15, "customer_history": 0.20}
    comp.overall = round(
        comp.payment * weights["payment"]
        + comp.order * weights["order"]
        + comp.shipment * weights["shipment"]
        + comp.refund * weights["refund"]
        + comp.customer_history * weights["customer_history"],
        4,
    )

    # -----------------------------------------------------------------------
    # 5. Determine enrichment version
    # -----------------------------------------------------------------------
    latest_enrichment = (
        db.query(CaseEnrichment)
        .filter(CaseEnrichment.case_id == case.case_id)
        .order_by(CaseEnrichment.version.desc())
        .first()
    )
    new_version = (latest_enrichment.version + 1) if latest_enrichment else 1

    # -----------------------------------------------------------------------
    # 6. Decide: ENRICHED or terminal failure (Correction #1, #4)
    # -----------------------------------------------------------------------
    payment_found = payment_status == LookupStatus.FOUND
    now = datetime.now(timezone.utc)

    if not payment_found:
        # Terminal failure — payment is mandatory critical enrichment.
        # Revert to INGESTED (Correction #1: do not leave stuck in ENRICHING).
        case.processing_state = ProcessingState.INGESTED

        error = ProcessingError(
            case_id=case.case_id,
            module="module_b",
            error_code="PAYMENT_NOT_FOUND",
            error_message=f"Critical payment lookup failed: {dispute.payment_id} status={payment_status.value}",
            retryable=(payment_status == LookupStatus.LOOKUP_FAILED),
        )
        db.add(error)

        audit = AuditLog(
            case_id=case.case_id,
            action="ENRICHMENT_FAILED",
            details=f"Payment {dispute.payment_id} {payment_status.value}. "
                    f"Case reverted to INGESTED. Manual retry available.",
        )
        db.add(audit)

        db.commit()
        logger.warning(f"Enrichment failed for case {case_id}: payment {payment_status.value}")

        return EnrichmentResult(
            case_id=case_id,
            status="FAILED",
            payment_status=payment_status,
            order_status=order_status,
            shipment_status=shipment_status,
            refund_status=refund_status,
            customer_history_status=customer_status,
            consistency_flags=[f.value if hasattr(f, 'value') else f for f in consistency_flags],
            version=new_version,
        )

    # -----------------------------------------------------------------------
    # 7. Payment found → persist enrichment (Correction #2: safe re-enrichment)
    #    Retrieve first, validate, THEN replace existing entity rows atomically.
    # -----------------------------------------------------------------------

    # Delete old entity rows for this case (safe because we already have fresh data)
    db.query(Payment).filter(Payment.case_id == case.case_id).delete()
    db.query(Order).filter(Order.case_id == case.case_id).delete()
    db.query(Shipment).filter(Shipment.case_id == case.case_id).delete()
    db.query(Refund).filter(Refund.case_id == case.case_id).delete()
    db.query(CustomerHistory).filter(CustomerHistory.case_id == case.case_id).delete()

    # Persist payment
    db.add(Payment(
        case_id=case.case_id,
        external_payment_id=payment_data.external_payment_id,
        external_order_id=payment_data.external_order_id,
        amount_minor=payment_data.amount_minor,
        currency=payment_data.currency,
        status=payment_data.status,
        method=payment_data.method,
        network=payment_data.network,
        captured=payment_data.captured,
        created_at_source=payment_data.created_at_source,
        fetched_at=now,
    ))

    # Persist order (if found)
    if order_data:
        db.add(Order(
            case_id=case.case_id,
            external_order_id=order_data.external_order_id,
            merchant_order_ref=order_data.merchant_order_ref,
            order_amount_minor=order_data.order_amount_minor,
            currency=order_data.currency,
            order_status=order_data.order_status,
            customer_ref_hash=order_data.customer_ref_hash,
            created_at_source=order_data.created_at_source,
            fetched_at=now,
        ))

    # Persist shipment (if found)
    if shipment_data:
        db.add(Shipment(
            case_id=case.case_id,
            external_order_id=shipment_data.external_order_id,
            shipment_id=shipment_data.shipment_id,
            courier=shipment_data.courier,
            tracking_id=shipment_data.tracking_id,
            dispatch_at=shipment_data.dispatch_at,
            delivery_at=shipment_data.delivery_at,
            delivery_status=shipment_data.delivery_status,
            delivery_address_hash=shipment_data.delivery_address_hash,
            recipient_confirmation=shipment_data.recipient_confirmation,
        ))

    # Persist refunds (zero or more)
    for refund in refund_list:
        db.add(Refund(
            case_id=case.case_id,
            external_refund_id=refund.external_refund_id,
            external_payment_id=refund.external_payment_id,
            refund_amount_minor=refund.refund_amount_minor,
            status=refund.status,
            refund_reason=refund.refund_reason,
            refund_at=refund.refund_at,
        ))

    # Persist customer history (if found)
    if customer_data:
        db.add(CustomerHistory(
            case_id=case.case_id,
            customer_ref_hash=customer_data.customer_ref_hash,
            account_age_days=customer_data.account_age_days,
            previous_order_count=customer_data.previous_order_count,
            previous_dispute_count=customer_data.previous_dispute_count,
            previous_refund_count=customer_data.previous_refund_count,
            refund_rate=customer_data.refund_rate,
            dispute_rate=customer_data.dispute_rate,
            snapshot_at=now,
        ))

    # Persist CaseEnrichment (append-only version history)
    timeline_dict = timeline.model_dump(mode="json")
    enrichment_summary = CaseEnrichment(
        case_id=case.case_id,
        version=new_version,
        payment_complete=comp.payment,
        order_complete=comp.order,
        shipment_complete=comp.shipment,
        refund_complete=comp.refund,
        customer_complete=comp.customer_history,
        overall_complete=comp.overall,
        consistency_flags=[f.value if hasattr(f, 'value') else f for f in consistency_flags],
        timeline_json=timeline_dict,
        created_at=now,
    )
    db.add(enrichment_summary)

    # -----------------------------------------------------------------------
    # 8. Transition to ENRICHED
    # -----------------------------------------------------------------------
    case.processing_state = ProcessingState.ENRICHED

    audit = AuditLog(
        case_id=case.case_id,
        action="CASE_ENRICHMENT_COMPLETED",
        details=(
            f"Enrichment v{new_version}: payment={payment_status.value}, "
            f"order={order_status.value}, shipment={shipment_status.value}, "
            f"refund={refund_status.value}, customer={customer_status.value}. "
            f"Completeness={comp.overall:.2%}. "
            f"Flags={[f.value if hasattr(f, 'value') else f for f in consistency_flags]}"
        ),
    )
    db.add(audit)

    db.commit()
    logger.info(f"Enrichment completed for case {case_id} (v{new_version})")

    return EnrichmentResult(
        case_id=case_id,
        status="ENRICHED",
        payment_status=payment_status,
        order_status=order_status,
        shipment_status=shipment_status,
        refund_status=refund_status,
        customer_history_status=customer_status,
        consistency_flags=[f.value if hasattr(f, 'value') else f for f in consistency_flags],
        timeline=timeline,
        completeness=comp,
        version=new_version,
    )
