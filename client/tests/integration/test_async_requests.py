from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

ASYNC_API_URL = os.environ.get("COFFEE_SYNC_ASYNC_API_URL", "http://localhost:8000")


def _reachable(url: str) -> bool:
    try:
        httpx.get(url + "/health", timeout=3.0).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session", autouse=True)
def require_async_api():
    if not _reachable(ASYNC_API_URL):
        pytest.skip(f"Async API not reachable at {ASYNC_API_URL}; run `make async-up` first")


def test_async_request_is_accepted_and_eventually_done(direct_url, reset_toxics):
    store = f"async-itest-{uuid.uuid4().hex[:12]}"

    created = httpx.post(
        ASYNC_API_URL + "/api/v1/payment-requests",
        json={
            "defaultStoreId": store,
            "payments": [
                {"coffeeType": "LATTE", "price": "3.50", "currency": "EUR", "loyaltyCardId": f"{store}-1"},
                {
                    "coffeeType": "ESPRESSO",
                    "price": "2.00",
                    "currency": "EUR",
                    "loyaltyCardId": f"{store}-2",
                },
            ],
        },
        timeout=5.0,
    )
    assert created.status_code == 202
    request_id = created.json()["requestId"]

    status = None
    for _ in range(30):
        status = httpx.get(
            ASYNC_API_URL + f"/api/v1/payment-requests/{request_id}",
            timeout=5.0,
        ).json()
        if status["status"] == "done":
            break
        time.sleep(1)

    assert status["status"] == "done"
    assert status["succeeded"] == 2
    assert status["failed"] == 0

    stored = httpx.get(
        direct_url + "/api/v1/payments",
        params={"storeId": store},
        headers={"Store-Id": store},
        timeout=5.0,
    ).json()
    assert len(stored) == 2

