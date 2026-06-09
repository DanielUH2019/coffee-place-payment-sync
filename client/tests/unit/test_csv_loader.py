import pytest

from coffee_sync.csv_loader import CsvFormatError, load_payments

HEADER = "store_id,coffee_type,price,currency,loyalty_card_id\n"


def write(tmp_path, content):
    p = tmp_path / "payments.csv"
    p.write_text(content, encoding="utf-8")
    return p


def test_loads_rows(tmp_path):
    path = write(tmp_path, HEADER + "store-9,LATTE,3.50,EUR,card-1\n")
    payments = load_payments(path, default_store_id="default")
    assert len(payments) == 1
    p = payments[0]
    assert p.store_id == "store-9"
    assert p.coffee_type == "LATTE"
    assert p.row_number == 1


def test_normalises_case_and_whitespace(tmp_path):
    path = write(tmp_path, HEADER + " store-1 , latte , 3.50 , eur , card-1 \n")
    p = load_payments(path, default_store_id="default")[0]
    assert p.coffee_type == "LATTE"
    assert p.currency == "EUR"
    assert p.store_id == "store-1"


def test_store_id_falls_back_to_default(tmp_path):
    body = "coffee_type,price,currency,loyalty_card_id\nLATTE,3.50,EUR,card-1\n"
    p = load_payments(write(tmp_path, body), default_store_id="fallback-store")[0]
    assert p.store_id == "fallback-store"


def test_idempotency_key_column_is_picked_up(tmp_path):
    body = (
        "coffee_type,price,currency,loyalty_card_id,idempotency_key\n"
        "LATTE,3.50,EUR,card-1,my-key\n"
    )
    p = load_payments(write(tmp_path, body), default_store_id="d")[0]
    assert p.idempotency_key_override == "my-key"


def test_missing_required_columns_raises(tmp_path):
    body = "coffee_type,price\nLATTE,3.50\n"
    with pytest.raises(CsvFormatError):
        load_payments(write(tmp_path, body), default_store_id="d")
