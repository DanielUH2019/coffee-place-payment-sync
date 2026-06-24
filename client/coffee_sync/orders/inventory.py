"""Simulated store inventory.

No inventory service exists upstream (the external app only does payments), so this
stands in for one. State is a plain in-process dict — fine for a single-worker demo.

ponytail: in-memory, per-process stock. If you ever run multiple workers or need stock
to survive a worker restart, back this with the Payments-style service or a DB.
"""

from __future__ import annotations

import logging
from collections import defaultdict

log = logging.getLogger("orders.inventory")

# Seed every (store, coffee) generously; the demo controls out-of-stock per-order.
_DEFAULT_STOCK = 100
_stock: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(lambda: _DEFAULT_STOCK))


class OutOfStock(Exception):
    pass


def reserve(store_id: str, items: list[tuple[str, int]]) -> None:
    """Decrement stock for each (coffee_type, qty). Raises OutOfStock if any is short."""
    store = _stock[store_id]
    # Check first so we never partially reserve.
    for coffee_type, qty in items:
        if store[coffee_type] < qty:
            raise OutOfStock(f"{coffee_type}: need {qty}, have {store[coffee_type]}")
    for coffee_type, qty in items:
        store[coffee_type] -= qty
    log.info("reserved %s at %s", items, store_id)


def release(store_id: str, items: list[tuple[str, int]]) -> None:
    """Compensation for reserve(): give the stock back."""
    store = _stock[store_id]
    for coffee_type, qty in items:
        store[coffee_type] += qty
    log.info("released %s at %s", items, store_id)
