"""
Module B database models — Transaction and Order Enrichment.

Tables: payments, orders, shipments, refunds, customer_history, case_enrichment.

Tenant isolation: external identifiers are NOT globally unique because different
merchants/providers may share external ID namespaces. Uniqueness is scoped via
case_id (which already belongs to exactly one merchant).

Money: all financial amounts are BIGINT minor units (paise/cents).
Timestamps: all TIMESTAMPTZ (timezone-aware UTC).
"""

from sqlalchemy import (
    Column, String, Boolean, BigInteger, Integer, DateTime,
    ForeignKey, Numeric, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSON
from app.core.database import Base


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        # Tenant-scoped: one payment record per case, external_payment_id
        # unique within a case (not globally — different merchants may share
        # external payment ID namespaces from separate Razorpay accounts).
        UniqueConstraint("case_id", "external_payment_id", name="uq_payments_case_ext_pay"),
    )

    payment_pk = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    external_payment_id = Column(String(120), index=True, nullable=False)
    external_order_id = Column(String(120), index=True, nullable=True)
    amount_minor = Column(BigInteger, nullable=False)
    currency = Column(String(3), nullable=False)
    status = Column(String(50), nullable=False)
    method = Column(String(50), nullable=True)
    network = Column(String(50), nullable=True)
    captured = Column(Boolean, nullable=True)
    created_at_source = Column(DateTime(timezone=True), nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False)

    case = relationship("Case")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("case_id", "external_order_id", name="uq_orders_case_ext_order"),
    )

    order_pk = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    external_order_id = Column(String(120), index=True, nullable=False)
    merchant_order_ref = Column(String(120), index=True, nullable=True)
    order_amount_minor = Column(BigInteger, nullable=True)
    currency = Column(String(3), nullable=True)
    order_status = Column(String(50), nullable=True)
    customer_ref_hash = Column(String(64), index=True, nullable=True)
    created_at_source = Column(DateTime(timezone=True), nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False)

    case = relationship("Case")


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        # shipment_id scoped to case — same logistics provider ID could
        # theoretically appear across merchants.
        UniqueConstraint("case_id", "shipment_id", name="uq_shipments_case_ship"),
    )

    shipment_pk = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    external_order_id = Column(String(120), index=True, nullable=False)
    shipment_id = Column(String(120), index=True, nullable=False)
    courier = Column(String(100), nullable=True)
    tracking_id = Column(String(150), index=True, nullable=True)
    dispatch_at = Column(DateTime(timezone=True), nullable=True)
    delivery_at = Column(DateTime(timezone=True), nullable=True)
    delivery_status = Column(String(50), nullable=True)
    delivery_address_hash = Column(String(64), nullable=True)
    recipient_confirmation = Column(Boolean, nullable=True)

    case = relationship("Case")


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("case_id", "external_refund_id", name="uq_refunds_case_ext_refund"),
    )

    refund_pk = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    external_refund_id = Column(String(120), index=True, nullable=False)
    external_payment_id = Column(String(120), index=True, nullable=False)
    refund_amount_minor = Column(BigInteger, nullable=False)
    status = Column(String(50), nullable=False)
    refund_reason = Column(String(255), nullable=True)
    refund_at = Column(DateTime(timezone=True), nullable=True)

    case = relationship("Case")


class CustomerHistory(Base):
    __tablename__ = "customer_history"
    __table_args__ = (
        # One customer history snapshot per case in MVP.
        UniqueConstraint("case_id", name="uq_customer_history_case"),
    )

    customer_history_pk = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    customer_ref_hash = Column(String(64), index=True, nullable=True)
    account_age_days = Column(Integer, nullable=True)
    previous_order_count = Column(Integer, nullable=True)
    previous_dispute_count = Column(Integer, nullable=True)
    previous_refund_count = Column(Integer, nullable=True)
    refund_rate = Column(Numeric(6, 5), nullable=True)
    dispute_rate = Column(Numeric(6, 5), nullable=True)
    snapshot_at = Column(DateTime(timezone=True), nullable=False)

    case = relationship("Case")


class CaseEnrichment(Base):
    __tablename__ = "case_enrichment"

    enrichment_pk = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.case_id"), index=True, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    payment_complete = Column(Numeric(5, 4), nullable=True)
    order_complete = Column(Numeric(5, 4), nullable=True)
    shipment_complete = Column(Numeric(5, 4), nullable=True)
    refund_complete = Column(Numeric(5, 4), nullable=True)
    customer_complete = Column(Numeric(5, 4), nullable=True)
    overall_complete = Column(Numeric(5, 4), nullable=True)
    consistency_flags = Column(JSON, nullable=False, default=list)
    timeline_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    case = relationship("Case")
