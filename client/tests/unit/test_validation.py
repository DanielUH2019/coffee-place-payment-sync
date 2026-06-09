from coffee_sync.models import Payment
from coffee_sync.validation import validate


def make(**overrides) -> Payment:
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


def test_valid_payment_has_no_errors():
    assert validate(make()) == []


def test_unknown_coffee_type_rejected():
    errors = validate(make(coffee_type="UNICORN_FRAPPE"))
    assert any("coffee_type" in e for e in errors)


def test_non_positive_price_rejected():
    assert any("greater than 0" in e for e in validate(make(price="0")))
    assert any("greater than 0" in e for e in validate(make(price="-1.00")))


def test_too_many_decimals_rejected():
    assert any("decimal places" in e for e in validate(make(price="3.555")))


def test_non_numeric_price_rejected():
    assert any("valid number" in e for e in validate(make(price="abc")))


def test_bad_currency_rejected():
    assert any("currency" in e for e in validate(make(currency="EURO")))
    assert any("currency" in e for e in validate(make(currency="eu")))


def test_missing_loyalty_card_rejected():
    assert any("loyalty_card_id" in e for e in validate(make(loyalty_card_id="")))


def test_missing_store_id_rejected():
    assert any("store_id" in e for e in validate(make(store_id="")))
