"""Domain models for the Order-Ahead workflow.

All types are plain dataclasses / str-enums so Temporal's default JSON data
converter can (de)serialize them across the workflow/activity boundary and into
event history.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OrderState(str, Enum):
    """Where the order is. The workflow is the single source of truth for this."""

    PLACED = "PLACED"
    VALIDATED = "VALIDATED"
    INVENTORY_RESERVED = "INVENTORY_RESERVED"
    PAID = "PAID"
    BREWING = "BREWING"
    READY = "READY"
    COLLECTED = "COLLECTED"
    DONE = "DONE"
    # terminal failure / unhappy paths
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"


TERMINAL_STATES = frozenset(
    {OrderState.DONE, OrderState.FAILED, OrderState.CANCELLED, OrderState.ABANDONED}
)


@dataclass
class OrderItem:
    coffee_type: str
    qty: int = 1


@dataclass
class OrderRequest:
    order_id: str
    store_id: str
    items: list[OrderItem]
    currency: str
    loyalty_card_id: str
    # Demo fault-injection knobs (driven per-order from the starter CLI). The real
    # transient-payment fault (F1) comes from Toxiproxy in front of the live API and
    # needs no flag. ponytail: per-order flags beat fragile worker-env wiring for a demo.
    simulate_out_of_stock: bool = False
    simulate_payment_decline: bool = False


@dataclass
class OrderStatus:
    """Returned by the workflow `status` query."""

    order_id: str
    state: str
    detail: str = ""
    payment_id: str | None = None


@dataclass
class PricedOrder:
    order_id: str
    total: str  # string to preserve exact decimals, like Payment.price
    currency: str


@dataclass
class ReserveResult:
    ok: bool
    detail: str = ""


@dataclass
class PaymentResult:
    payment_id: str | None
    created: bool
    attempts: int = 1
