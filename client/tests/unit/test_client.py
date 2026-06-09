import httpx
import pytest
import respx

from coffee_sync.client import PaymentsClient, PermanentError
from coffee_sync.config import PAYMENTS_PATH
from coffee_sync.models import Payment

BASE = "http://central.test"
URL = BASE + PAYMENTS_PATH


def make_payment(**overrides) -> Payment:
    base = dict(
        store_id="store-1",
        coffee_type="LATTE",
        price="3.50",
        currency="EUR",
        loyalty_card_id="card-1",
        row_number=1,
    )
    base.update(overrides)
    return Payment(**base)


def client() -> PaymentsClient:
    # Zero backoff so retry tests don't actually sleep.
    return PaymentsClient(base_url=BASE, max_retries=5, backoff_initial=0, backoff_max=0)


@respx.mock
def test_201_is_created():
    respx.post(URL).mock(return_value=httpx.Response(201, json={"paymentId": "p1"}))
    with client() as c:
        result = c.send_payment(make_payment())
    assert result.created is True
    assert result.status_code == 201
    assert result.payment_id == "p1"


@respx.mock
def test_200_is_replayed():
    respx.post(URL).mock(return_value=httpx.Response(200, json={"paymentId": "p1"}))
    with client() as c:
        result = c.send_payment(make_payment())
    assert result.created is False
    assert result.status_code == 200


@respx.mock
def test_idempotency_header_sent_and_stable():
    route = respx.post(URL).mock(return_value=httpx.Response(201, json={"paymentId": "p1"}))
    p = make_payment()
    with client() as c:
        c.send_payment(p)
        c.send_payment(p)
    keys = {req.headers["Idempotency-Key"] for req, _ in route.calls}
    store_ids = {req.headers["Store-Id"] for req, _ in route.calls}
    assert len(keys) == 1  # same row -> same key both times
    assert store_ids == {"store-1"}


@respx.mock
def test_5xx_is_retried_then_succeeds():
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(500, json={"error": "boom"}),
            httpx.Response(503, json={"error": "again"}),
            httpx.Response(201, json={"paymentId": "p1"}),
        ]
    )
    with client() as c:
        result = c.send_payment(make_payment())
    assert result.created is True
    assert result.attempts == 3
    assert route.call_count == 3


@respx.mock
def test_timeout_is_retried_then_succeeds():
    route = respx.post(URL).mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(201, json={"paymentId": "p1"})]
    )
    with client() as c:
        result = c.send_payment(make_payment())
    assert result.created is True
    assert route.call_count == 2


@respx.mock
def test_4xx_is_permanent_not_retried():
    route = respx.post(URL).mock(return_value=httpx.Response(400, json={"errors": ["bad"]}))
    with client() as c:
        with pytest.raises(PermanentError) as exc:
            c.send_payment(make_payment())
    assert exc.value.status_code == 400
    assert route.call_count == 1  # not retried


@respx.mock
def test_retries_exhausted_reraises_transient():
    respx.post(URL).mock(return_value=httpx.Response(500))
    with client() as c:
        with pytest.raises(Exception):
            c.send_payment(make_payment())
