from coffee_sync.idempotency import idempotency_key
from coffee_sync.models import Payment


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


def test_key_is_deterministic():
    assert idempotency_key(make()) == idempotency_key(make())


def test_different_content_yields_different_key():
    assert idempotency_key(make()) != idempotency_key(make(price="4.00"))
    assert idempotency_key(make()) != idempotency_key(make(loyalty_card_id="card-2"))


def test_override_takes_precedence():
    p = make(idempotency_key_override="explicit-key-123")
    assert idempotency_key(p) == "explicit-key-123"


def test_key_is_sha256_hex():
    key = idempotency_key(make())
    assert len(key) == 64
    int(key, 16)  # valid hex
