"""Unit tests for the take_payment activity's error mapping.

The activity's job is to translate the payments stack into Temporal's retry contract:
4xx / simulated decline -> non-retryable ApplicationError; 5xx / timeout -> plain
exception that propagates so Temporal retries. These are what make F1 vs F2 behave.
"""

import httpx
import pytest
import respx
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from coffee_sync import config
from coffee_sync.orders.activities import take_payment, validate_and_price
from coffee_sync.orders.models import OrderItem, OrderRequest, PricedOrder

URL = config.DEFAULT_BASE_URL + config.PAYMENTS_PATH


def _order(**kw) -> OrderRequest:
    return OrderRequest(
        order_id="order-1",
        store_id="store-london-01",
        items=[OrderItem("LATTE", 2), OrderItem("COLD_BREW", 1)],
        currency="EUR",
        loyalty_card_id="card-1001",
        **kw,
    )


_PRICED = PricedOrder(order_id="order-1", total="11.00", currency="EUR")


@respx.mock
def test_take_payment_created_returns_payment_id():
    respx.post(URL).mock(return_value=httpx.Response(201, json={"paymentId": "p1"}))
    res = ActivityEnvironment().run(take_payment, _order(), _PRICED)
    assert res.payment_id == "p1"
    assert res.created is True


@respx.mock
def test_take_payment_idempotent_replay_is_success():
    respx.post(URL).mock(return_value=httpx.Response(200, json={"paymentId": "p1"}))
    res = ActivityEnvironment().run(take_payment, _order(), _PRICED)
    assert res.payment_id == "p1"
    assert res.created is False


@respx.mock
def test_take_payment_4xx_is_non_retryable():
    respx.post(URL).mock(return_value=httpx.Response(400, json={"error": "bad request"}))
    with pytest.raises(ApplicationError) as ei:
        ActivityEnvironment().run(take_payment, _order(), _PRICED)
    assert ei.value.non_retryable is True
    assert ei.value.type == "PaymentDeclined"


@respx.mock
def test_take_payment_5xx_propagates_as_retryable():
    respx.post(URL).mock(return_value=httpx.Response(503, text="upstream down"))
    with pytest.raises(Exception) as ei:
        ActivityEnvironment().run(take_payment, _order(), _PRICED)
    # Not an ApplicationError -> Temporal treats it as retryable.
    assert not isinstance(ei.value, ApplicationError)


def test_take_payment_simulated_decline_is_non_retryable():
    with pytest.raises(ApplicationError) as ei:
        ActivityEnvironment().run(take_payment, _order(simulate_payment_decline=True), _PRICED)
    assert ei.value.non_retryable is True


def test_validate_and_price_sums_total():
    priced = ActivityEnvironment().run(validate_and_price, _order())
    # 2*LATTE(3.50) + 1*COLD_BREW(4.00) = 11.00
    assert priced.total == "11.00"


def test_validate_and_price_rejects_unknown_coffee():
    bad = OrderRequest(
        order_id="o", store_id="s", items=[OrderItem("UNICORN_BREW", 1)],
        currency="EUR", loyalty_card_id="card-1",
    )
    with pytest.raises(ApplicationError) as ei:
        ActivityEnvironment().run(validate_and_price, bad)
    assert ei.value.non_retryable is True
