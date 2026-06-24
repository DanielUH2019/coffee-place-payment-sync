"""Simulated loyalty-points service (the assignment's "hw1 loyalty", which has no
upstream endpoint in this repo). In-process balances; logs accruals.
"""

from __future__ import annotations

import logging
from collections import defaultdict

log = logging.getLogger("orders.loyalty")

_balances: dict[str, int] = defaultdict(int)


def accrue(loyalty_card_id: str, points: int) -> int:
    _balances[loyalty_card_id] += points
    log.info("accrued %d points to %s (balance=%d)", points, loyalty_card_id, _balances[loyalty_card_id])
    return _balances[loyalty_card_id]
