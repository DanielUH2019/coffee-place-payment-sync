"""Workflow tests for the F1–F5 scenarios + happy path.

These use Temporal's time-skipping test environment with mocked activities, so the
brew-SLA / pickup-expiry / substitution timers fire instantly and no Docker, real
clock, or live Payments API is needed. F6 (worker crash/resume) is covered by the
live demo, not here.
"""

import asyncio
import uuid

from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from coffee_sync.orders.models import (
    OrderItem,
    OrderRequest,
    PaymentResult,
    PricedOrder,
    ReserveResult,
)
from coffee_sync.orders.workflow import OrderWorkflow

TASK_QUEUE = "orders-test"


def make_activities(rec, *, payment_fail_times=0, payment_decline=False):
    """Mocked activities registered under the same names the real ones use, so the
    workflow dispatches to these. `rec` records calls; knobs drive each scenario."""
    state = {"payment_calls": 0}

    @activity.defn(name="validate_and_price")
    async def validate_and_price(order: OrderRequest) -> PricedOrder:
        rec.append("validate_and_price")
        return PricedOrder(order.order_id, "11.00", "EUR")

    @activity.defn(name="reserve_inventory")
    async def reserve_inventory(order: OrderRequest) -> ReserveResult:
        rec.append("reserve_inventory")
        if order.simulate_out_of_stock:
            return ReserveResult(ok=False, detail="out of stock")
        return ReserveResult(ok=True)

    @activity.defn(name="release_inventory")
    async def release_inventory(order: OrderRequest) -> None:
        rec.append("release_inventory")

    @activity.defn(name="take_payment")
    async def take_payment(order: OrderRequest, priced: PricedOrder) -> PaymentResult:
        rec.append("take_payment")
        if payment_decline:
            raise ApplicationError("declined", type="PaymentDeclined", non_retryable=True)
        state["payment_calls"] += 1
        if state["payment_calls"] <= payment_fail_times:
            raise RuntimeError("transient payment timeout")  # retryable -> Temporal retries
        return PaymentResult(payment_id="pay-1", created=True)

    @activity.defn(name="refund_payment")
    async def refund_payment(payment_id, store_id) -> None:
        rec.append("refund_payment")

    @activity.defn(name="enqueue_barista_ticket")
    async def enqueue_barista_ticket(order: OrderRequest) -> str:
        rec.append("enqueue_barista_ticket")
        return "ticket-1"

    @activity.defn(name="notify_customer")
    async def notify_customer(card, msg) -> None:
        rec.append("notify_customer")

    @activity.defn(name="accrue_loyalty")
    async def accrue_loyalty(card, drinks) -> int:
        rec.append("accrue_loyalty")
        return drinks * 10

    return [
        validate_and_price, reserve_inventory, release_inventory, take_payment,
        refund_payment, enqueue_barista_ticket, notify_customer, accrue_loyalty,
    ]


def _order(**kw) -> OrderRequest:
    return OrderRequest(
        order_id=f"order-{uuid.uuid4().hex[:8]}",
        store_id="store-london-01",
        items=[OrderItem("LATTE", 2), OrderItem("COLD_BREW", 1)],
        currency="EUR",
        loyalty_card_id="card-1001",
        **kw,
    )


async def _run(activities, order, signaller=None):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE,
            workflows=[OrderWorkflow], activities=activities,
        ):
            handle = await env.client.start_workflow(
                OrderWorkflow.run, order, id=order.order_id, task_queue=TASK_QUEUE
            )
            if signaller:
                await signaller(handle)
            return await handle.result()


async def _collect_happy(handle):
    await handle.signal(OrderWorkflow.order_ready)
    await handle.signal(OrderWorkflow.customer_collected)


def test_happy_path_reaches_done_and_accrues_loyalty():
    rec = []
    result = asyncio.run(_run(make_activities(rec), _order(), _collect_happy))
    assert result.state == "DONE"
    assert "accrue_loyalty" in rec
    assert result.payment_id == "pay-1"


def test_f1_payment_times_out_then_succeeds_exactly_once_effect():
    rec = []
    result = asyncio.run(_run(make_activities(rec, payment_fail_times=2), _order(), _collect_happy))
    assert result.state == "DONE"
    # Retried: 2 failures + 1 success. Idempotency key (order_id) keeps it one charge.
    assert rec.count("take_payment") == 3


def test_f2_payment_declined_releases_inventory_and_fails():
    rec = []
    result = asyncio.run(_run(make_activities(rec, payment_decline=True), _order()))
    assert result.state == "FAILED"
    assert "release_inventory" in rec   # inventory released (compensation)
    assert "notify_customer" in rec     # customer notified
    assert "accrue_loyalty" not in rec  # no loyalty accrued
    assert "refund_payment" not in rec  # nothing was charged


def test_f3_out_of_stock_no_response_cancels():
    rec = []
    # sub deadline elapses with no substitution signal -> cancel
    result = asyncio.run(_run(make_activities(rec), _order(simulate_out_of_stock=True, sub_deadline_seconds=60)))
    assert result.state == "CANCELLED"
    assert "take_payment" not in rec  # never charged (payment is after inventory)


def test_f3_out_of_stock_substitution_accepted_proceeds():
    rec = []

    async def accept_then_collect(handle):
        await handle.signal(OrderWorkflow.substitution_response, True)
        await _collect_happy(handle)

    result = asyncio.run(_run(make_activities(rec), _order(simulate_out_of_stock=True), accept_then_collect))
    assert result.state == "DONE"
    assert rec.count("reserve_inventory") == 2  # first fails, retry after substitution


def test_f4_cancel_before_drinks_made_refunds_and_releases():
    rec = []

    async def cancel_after_paid(handle):
        for _ in range(200):
            st = await handle.query(OrderWorkflow.status)
            if st.state in ("PAID", "BREWING"):
                break
            await asyncio.sleep(0.02)
        await handle.signal(OrderWorkflow.customer_cancel)

    result = asyncio.run(_run(make_activities(rec), _order(), cancel_after_paid))
    assert result.state == "CANCELLED"
    assert "refund_payment" in rec
    assert "release_inventory" in rec
    assert "accrue_loyalty" not in rec


def test_f5_never_collected_is_abandoned():
    rec = []

    async def make_but_dont_collect(handle):
        await handle.signal(OrderWorkflow.order_ready)

    result = asyncio.run(_run(make_activities(rec), _order(pickup_ttl_seconds=1800), make_but_dont_collect))
    assert result.state == "ABANDONED"
    assert "accrue_loyalty" not in rec
    assert "refund_payment" not in rec  # policy: drinks made -> no refund
