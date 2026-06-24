"""OrderWorkflow — the durable state machine for one order-ahead order.

This is the heart of the assignment. The workflow is the single source of truth for
where an order is. It coordinates activities, reacts to external signals (barista,
customer), and drives timers (substitution deadline, brew SLA, pickup expiry). All
side effects live in activities; the workflow body is deterministic so Temporal can
replay it after a worker crash (F6) and land in the exact same state.

Compensation (saga): as each reversible step succeeds we push a compensation onto a
stack. On failure or a pre-make cancel we unwind the stack in reverse (refund, then
release inventory).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from dataclasses import replace

    from .activities import (
        accrue_loyalty,
        enqueue_barista_ticket,
        notify_customer,
        refund_payment,
        release_inventory,
        reserve_inventory,
        take_payment,
        validate_and_price,
    )
    from .models import OrderRequest, OrderState, OrderStatus

# Non-payment activities: retry a few times for transient blips, never retry business
# rejections (ApplicationError(non_retryable=True) is honored regardless).
_DEFAULT_RETRY = RetryPolicy(maximum_attempts=3)
# Payment: back off and keep retrying transient timeouts/5xx (F1); never retry a decline.
_PAYMENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=0,  # unlimited; bounded by the activity start_to_close timeout
    non_retryable_error_types=["PaymentDeclined"],
)


@workflow.defn
class OrderWorkflow:
    def __init__(self) -> None:
        self._order_id = ""
        self._state = OrderState.PLACED
        self._detail = ""
        self._payment_id: str | None = None
        self._compensations: list[tuple[str, object]] = []
        # external-event state
        self._cancel_requested = False
        self._barista_started = False
        self._ready = False
        self._collected = False
        self._substitution: bool | None = None

    # ── signals (external events) ───────────────────────────────────────────
    @workflow.signal
    def customer_cancel(self) -> None:
        self._cancel_requested = True

    @workflow.signal
    def barista_started(self) -> None:
        self._barista_started = True

    @workflow.signal
    def order_ready(self) -> None:
        self._ready = True

    @workflow.signal
    def customer_collected(self) -> None:
        self._collected = True

    @workflow.signal
    def substitution_response(self, accepted: bool) -> None:
        self._substitution = accepted

    # ── query ───────────────────────────────────────────────────────────────
    @workflow.query
    def status(self) -> OrderStatus:
        return OrderStatus(
            order_id=self._order_id,
            state=self._state.value,
            detail=self._detail,
            payment_id=self._payment_id,
        )

    # ── main run ──────────────────────────────────────────────────────────────
    @workflow.run
    async def run(self, order: OrderRequest) -> OrderStatus:
        self._order_id = order.order_id

        # 1. Validate & price.
        try:
            priced = await self._act(validate_and_price, order)
        except ActivityError:
            return self._set(OrderState.FAILED, "validation failed")
        self._set(OrderState.VALIDATED)
        if self._cancel_requested:  # nothing reserved/charged yet
            return self._set(OrderState.CANCELLED, "cancelled before reservation")

        # 2. Reserve inventory (F3: out of stock -> substitution offer).
        reserve = await self._act(reserve_inventory, order)
        if not reserve.ok:
            await self._act(notify_customer, order.loyalty_card_id,
                            f"Out of stock ({reserve.detail}). Accept a substitution?")
            if not await self._wait_substitution(order):
                return self._set(OrderState.CANCELLED, "out of stock; no substitution accepted")
            reserve = await self._act(reserve_inventory, replace(order, simulate_out_of_stock=False))
            if not reserve.ok:
                return self._set(OrderState.FAILED, "substitute unavailable")
        self._push("release_inventory", release_inventory, order)
        self._set(OrderState.INVENTORY_RESERVED)
        if self._cancel_requested:
            return await self._cancel(order, "cancelled before payment")

        # 3. Take payment (F1 retried with backoff; F2 decline -> compensate + FAILED).
        try:
            pay = await self._act(take_payment, order, priced,
                                  start_to_close=timedelta(seconds=30), retry=_PAYMENT_RETRY)
        except ActivityError:
            await self._compensate()
            await self._act(notify_customer, order.loyalty_card_id, "Payment declined — order cancelled")
            return self._set(OrderState.FAILED, "payment declined")
        self._payment_id = pay.payment_id
        self._push("refund", refund_payment, self._payment_id, order.store_id)
        self._set(OrderState.PAID)
        if self._cancel_requested:
            return await self._cancel(order, "cancelled after payment (F4)")

        # 4. Hand to barista; wait for the drinks, racing the brew-SLA timer + cancel.
        await self._act(enqueue_barista_ticket, order)
        self._set(OrderState.BREWING)
        await self._await_brewing(order)
        if self._cancel_requested and not self._ready:  # F4: cancel before drinks made
            return await self._cancel(order, "cancelled before drinks made (F4)")

        # 5. Drinks made -> notify, then wait for pickup racing the expiry timer (F5).
        self._set(OrderState.READY)
        await self._act(notify_customer, order.loyalty_card_id, "Ready for pickup!")
        if not await self._await_pickup(order):
            # Policy: drinks already made & paid -> no refund (waste borne by store).
            await self._act(notify_customer, order.loyalty_card_id, "Order not collected — abandoned")
            return self._set(OrderState.ABANDONED, "pickup expired; drinks wasted, no refund")

        # 6. Collected -> accrue loyalty and close.
        self._set(OrderState.COLLECTED)
        drinks = sum(item.qty for item in order.items)
        await self._act(accrue_loyalty, order.loyalty_card_id, drinks)
        return self._set(OrderState.DONE)

    # ── waits (timers raced against signals) ────────────────────────────────
    async def _wait_substitution(self, order: OrderRequest) -> bool:
        try:
            await workflow.wait_condition(
                lambda: self._substitution is not None or self._cancel_requested,
                timeout=timedelta(seconds=order.sub_deadline_seconds),
            )
        except asyncio.TimeoutError:
            return False  # deadline passed -> treat as declined
        return bool(self._substitution) and not self._cancel_requested

    async def _await_brewing(self, order: OrderRequest) -> None:
        try:
            await workflow.wait_condition(
                lambda: self._ready or self._cancel_requested,
                timeout=timedelta(seconds=order.brew_sla_seconds),
            )
        except asyncio.TimeoutError:
            # Brew SLA breached: escalate to a manager, then keep waiting.
            await self._act(notify_customer, "manager",
                            f"Brew SLA breached for {order.order_id} — escalating (comp/refund)")
            self._set(OrderState.BREWING, "SLA breached, escalated")
            await workflow.wait_condition(lambda: self._ready or self._cancel_requested)

    async def _await_pickup(self, order: OrderRequest) -> bool:
        # After the drinks are made, a cancel is refused — we only race collect vs expiry.
        try:
            await workflow.wait_condition(
                lambda: self._collected,
                timeout=timedelta(seconds=order.pickup_ttl_seconds),
            )
            return True
        except asyncio.TimeoutError:
            return False

    # ── compensation / helpers ───────────────────────────────────────────────
    def _push(self, label: str, fn, *args) -> None:
        self._compensations.append((label, lambda: self._act(fn, *args)))

    async def _compensate(self) -> None:
        for label, fn in reversed(self._compensations):
            try:
                await fn()
            except Exception as e:  # compensation is best-effort
                workflow.logger.warning("compensation %s failed: %s", label, e)
        self._compensations.clear()

    async def _cancel(self, order: OrderRequest, detail: str) -> OrderStatus:
        await self._compensate()
        await self._act(notify_customer, order.loyalty_card_id, f"Order cancelled: {detail}")
        return self._set(OrderState.CANCELLED, detail)

    async def _act(self, fn, *args, start_to_close: timedelta = timedelta(seconds=10), retry=None):
        return await workflow.execute_activity(
            fn,
            args=list(args),
            start_to_close_timeout=start_to_close,
            retry_policy=retry or _DEFAULT_RETRY,
        )

    def _set(self, state: OrderState, detail: str = "") -> OrderStatus:
        self._state = state
        self._detail = detail
        workflow.logger.info("order %s -> %s %s", self._order_id, state.value, detail)
        return self.status()
