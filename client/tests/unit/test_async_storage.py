from coffee_sync.storage import PAYMENT_FAILED, PAYMENT_PENDING, _row_from_input
from coffee_sync.schemas import BulkPaymentIn


def test_storage_row_keeps_valid_payment_pending_with_deterministic_key():
    row = _row_from_input(
        BulkPaymentIn(coffeeType="latte", price="3.50", currency="eur", loyaltyCardId="card-1"),
        1,
        "store-1",
    )

    assert row["status"] == PAYMENT_PENDING
    assert row["store_id"] == "store-1"
    assert row["coffee_type"] == "LATTE"
    assert row["currency"] == "EUR"
    assert row["idempotency_key"]


def test_storage_row_keeps_invalid_payment_failed_without_losing_raw_price():
    row = _row_from_input(
        BulkPaymentIn(coffeeType="UNICORN", price="not-money", currency="EUR", loyaltyCardId="card-1"),
        1,
        "store-1",
    )

    assert row["status"] == PAYMENT_FAILED
    assert row["price"] == "not-money"
    assert "coffee_type" in row["error"]
    assert "price" in row["error"]

