"""Load and normalise the Coffee Place notebook CSV.

Expected columns (header row, case-insensitive):
    coffee_type, price, currency, loyalty_card_id
Optional columns:
    store_id            - per-row store; falls back to the supplied default
    idempotency_key     - explicit key; otherwise derived deterministically

Values are trimmed; coffee_type and currency are upper-cased so a notebook written
as "latte"/"eur" still matches the server's enum and ISO code rules.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .models import Payment

REQUIRED_COLUMNS = {"coffee_type", "price", "currency", "loyalty_card_id"}


class CsvFormatError(Exception):
    pass


def load_payments(path: str | Path, default_store_id: str) -> list[Payment]:
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise CsvFormatError(f"{path} is empty (no header row)")

        fieldnames = {name.strip().lower() for name in reader.fieldnames}
        missing = REQUIRED_COLUMNS - fieldnames
        if missing:
            raise CsvFormatError(
                f"{path} is missing required columns: {sorted(missing)} "
                f"(found {sorted(fieldnames)})"
            )

        payments: list[Payment] = []
        for index, raw in enumerate(reader, start=1):
            row = {(k.strip().lower() if k else k): (v.strip() if v else "") for k, v in raw.items()}
            store_id = row.get("store_id") or default_store_id
            override = row.get("idempotency_key") or None
            payments.append(
                Payment(
                    store_id=store_id,
                    coffee_type=row.get("coffee_type", "").upper(),
                    price=row.get("price", ""),
                    currency=row.get("currency", "").upper(),
                    loyalty_card_id=row.get("loyalty_card_id", ""),
                    row_number=index,
                    idempotency_key_override=override,
                )
            )
        return payments
