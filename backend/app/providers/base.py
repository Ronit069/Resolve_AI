"""
Provider adapter base classes and error types.

All provider adapters implement these ABCs, returning canonical internal
data structures (defined in app.schemas.module_b).

Error semantics:
  - ProviderNotFoundError: the entity genuinely does not exist at the source.
    This is a PERMANENT condition — do not retry.
  - ProviderUnavailableError: transient failure (timeout, 5xx, network).
    This IS retryable via Celery exponential backoff.
"""

from abc import ABC, abstractmethod
from typing import Optional, List
from dataclasses import dataclass

from app.schemas.module_b import (
    PaymentData,
    OrderData,
    ShipmentData,
    RefundData,
    CustomerHistoryData,
)


# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------

class ProviderNotFoundError(Exception):
    """Entity does not exist at the source. Permanent — do not retry."""

    def __init__(self, entity_type: str, identifier: str, message: str = ""):
        self.entity_type = entity_type
        self.identifier = identifier
        super().__init__(message or f"{entity_type} '{identifier}' not found at provider")


class ProviderUnavailableError(Exception):
    """Transient provider failure. Retryable via exponential backoff."""

    def __init__(self, entity_type: str, identifier: str, message: str = ""):
        self.entity_type = entity_type
        self.identifier = identifier
        super().__init__(message or f"Provider unavailable when fetching {entity_type} '{identifier}'")


# ---------------------------------------------------------------------------
# Abstract provider interfaces
# ---------------------------------------------------------------------------

class PaymentProvider(ABC):
    @abstractmethod
    def get_payment(self, payment_id: str) -> PaymentData:
        """Retrieve canonical payment data. Raises ProviderNotFoundError or ProviderUnavailableError."""
        ...

class OrderProvider(ABC):
    @abstractmethod
    def get_order(self, order_id: str) -> OrderData:
        """Retrieve canonical order data. Raises ProviderNotFoundError or ProviderUnavailableError."""
        ...

class ShipmentProvider(ABC):
    @abstractmethod
    def get_shipment(self, order_id: str) -> ShipmentData:
        """Retrieve canonical shipment data. Raises ProviderNotFoundError or ProviderUnavailableError."""
        ...

class RefundProvider(ABC):
    @abstractmethod
    def get_refunds(self, payment_id: str) -> List[RefundData]:
        """Retrieve zero or more refunds. Empty list = no refunds (not an error).
        Raises ProviderNotFoundError (payment unknown) or ProviderUnavailableError."""
        ...

class CustomerHistoryProvider(ABC):
    @abstractmethod
    def get_customer_history(self, customer_ref_hash: str) -> CustomerHistoryData:
        """Retrieve privacy-minimized customer history aggregates.
        Raises ProviderNotFoundError or ProviderUnavailableError."""
        ...


# ---------------------------------------------------------------------------
# Provider bundle — groups all five providers for injection
# ---------------------------------------------------------------------------

@dataclass
class ProviderBundle:
    """Convenience container passed to the enrichment service."""
    payment: PaymentProvider
    order: OrderProvider
    shipment: ShipmentProvider
    refund: RefundProvider
    customer_history: CustomerHistoryProvider
