"""Simulated customer push notifications (stands in for the StarHarbour app push)."""

from __future__ import annotations

import logging

log = logging.getLogger("orders.notifications")


def push(customer: str, message: str) -> None:
    log.info("PUSH -> %s: %s", customer, message)
