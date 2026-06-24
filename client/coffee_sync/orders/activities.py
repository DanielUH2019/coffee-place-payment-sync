"""Temporal activities — the only place the workflow touches the outside world.

Activities are plain (sync) functions; the worker runs them in a thread pool. They
reuse the existing reliable payments stack (PaymentsClient, deterministic idempotency,
validation) and the simulated inventory/loyalty/notification services.

Error contract (this is what makes F1 vs F2 work):
  * Transient payment failures (timeouts, 5xx) propagate as ordinary exceptions ->
    Temporal retries the activity per its RetryPolicy (F1).
  * Permanent failures (4xx decline, invalid order, out-of-stock) raise
    ApplicationError(non_retryable=True) so Temporal does NOT retry; the workflow
    catches them and runs its compensation / branch logic (F2, F3).
"""

from __future__ import annotations

from decimal import Decimal

from temporalio import activity
from temporalio.exceptions import ApplicationError

from .. import config
from ..client import PaymentsClient, PermanentError
from ..models import Payment
from ..validation import validate
from . import inventory, loyalty, notifications
from .models import OrderRequest, PaymentResult, PricedOrder, ReserveResult

# Menu prices. No pricing service upstream, so this is the source of truth.
PRICES: dict[str, Decimal] = {
    "ESPRESSO": Decimal("2.00"),
    "DOUBLE_ESPRESSO": Decimal("2.80"),
    "AMERICANO": Decimal("2.50"),
    "LATTE": Decimal("3.50"),
    "CAPPUCCINO": Decimal("3.20"),
    "FLAT_WHITE": Decimal("3.40"),
    "MOCHA": Decimal("3.80"),
    "CORTADO": Decimal("3.00"),
    "MACCHIATO": Decimal("2.90"),
    "COLD_BREW": Decimal("4.00"),
}

POINTS_PER_DRINK = 10


def _item_pairs(order: OrderRequest) -> list[tuple[str, int]]:
    return [(item.coffee_type, item.qty) for item in order.items]


@activity.defn
def validate_and_price(order: OrderRequest) -> PricedOrder:
    """Step 1: validate (known coffee types, valid currency/card) and price the order."""
    errors: list[str] = []
    total = Decimal("0")
    if not order.items:
        errors.append("order has no items")
    for item in order.items:
        if item.qty <= 0:
            errors.append(f"item {item.coffee_type} has non-positive qty {item.qty}")
        if item.coffee_type not in PRICES:
            errors.append(f"unknown coffee_type '{item.coffee_type}'")
        else:
            total += PRICES[item.coffee_type] * item.qty

    # Reuse the existing validator for currency / loyalty-card / price rules, using a
    # representative Payment carrying the order total and first coffee type.
    if order.items and order.items[0].coffee_type in PRICES:
        probe = Payment(
            store_id=order.store_id,
            coffee_type=order.items[0].coffee_type,
            price=f"{total:.2f}",
            currency=order.currency,
            loyalty_card_id=order.loyalty_card_id,
            row_number=0,
        )
        errors.extend(validate(probe))

    if errors:
        raise ApplicationError("; ".join(errors), type="InvalidOrder", non_retryable=True)
    return PricedOrder(order_id=order.order_id, total=f"{total:.2f}", currency=order.currency)


@activity.defn
def reserve_inventory(order: OrderRequest) -> ReserveResult:
    """Step 2: reserve stock. Out-of-stock is a business outcome, returned (not raised),
    so the workflow can branch into the substitution flow (F3)."""
    if order.simulate_out_of_stock:
        return ReserveResult(ok=False, detail="simulated out of stock")
    try:
        inventory.reserve(order.store_id, _item_pairs(order))
    except inventory.OutOfStock as e:
        return ReserveResult(ok=False, detail=str(e))
    return ReserveResult(ok=True)


@activity.defn
def release_inventory(order: OrderRequest) -> None:
    """Compensation for reserve_inventory()."""
    inventory.release(order.store_id, _item_pairs(order))


@activity.defn
def take_payment(order: OrderRequest, priced: PricedOrder) -> PaymentResult:
    """Step 3: charge via the live Payments API with a deterministic Idempotency-Key.

    The key is the order_id, so every retry — and a full workflow replay — reuses it:
    exactly one charge even when a timeout hides a write that actually landed."""
    if order.simulate_payment_decline:
        raise ApplicationError("payment declined", type="PaymentDeclined", non_retryable=True)

    payment = Payment(
        store_id=order.store_id,
        coffee_type=order.items[0].coffee_type,
        price=priced.total,
        currency=order.currency,
        loyalty_card_id=order.loyalty_card_id,
        row_number=0,
        idempotency_key_override=order.order_id,
    )
    try:
        # max_retries=1: one HTTP attempt per activity invocation so Temporal owns the
        # backoff/retry loop (visible in event history). Idempotency keeps it exactly-once.
        with PaymentsClient(max_retries=1) as client:
            result = client.send_payment(payment)
    except PermanentError as e:
        raise ApplicationError(str(e), type="PaymentDeclined", non_retryable=True) from e
    # TransientError / httpx transport errors propagate -> Temporal retries (F1).
    return PaymentResult(payment_id=result.payment_id, created=result.created, attempts=result.attempts)


@activity.defn
def refund_payment(payment_id: str | None, store_id: str) -> None:
    """Compensation for take_payment() (simulated — the Payments API is append-only)."""
    activity.logger.info("REFUND issued for payment %s at %s", payment_id, store_id)


@activity.defn
def enqueue_barista_ticket(order: OrderRequest) -> str:
    """Step 4: hand the ticket to the barista queue (simulated)."""
    ticket = f"ticket-{order.order_id}"
    activity.logger.info("barista ticket %s queued: %s", ticket, _item_pairs(order))
    return ticket


@activity.defn
def notify_customer(loyalty_card_id: str, message: str) -> None:
    notifications.push(loyalty_card_id, message)


@activity.defn
def accrue_loyalty(loyalty_card_id: str, drinks: int) -> int:
    """Step 6: accrue loyalty points after pickup."""
    return loyalty.accrue(loyalty_card_id, drinks * POINTS_PER_DRINK)


ALL_ACTIVITIES = [
    validate_and_price,
    reserve_inventory,
    release_inventory,
    take_payment,
    refund_payment,
    enqueue_barista_ticket,
    notify_customer,
    accrue_loyalty,
]
